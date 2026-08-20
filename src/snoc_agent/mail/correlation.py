"""RFC/header/marker correlation with conflict detection and weak subject fallback."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from snoc_agent.db.models import BusinessRequest, EmailMessage, OutboxMessage
from snoc_agent.db.repositories import (
    ClarificationRepository,
    ConversationRepository,
    EmailRepository,
    RequestRepository,
)
from snoc_agent.domain.entities import CorrelationResult
from snoc_agent.domain.enums import CorrelationStrength
from snoc_agent.mail.markers import parse_request_references
from snoc_agent.mail.parser import ParsedEmail


def _request_for_message(session: Session, message: EmailMessage) -> BusinessRequest | None:
    clarification = ClarificationRepository(session).by_outbound_rfc_id(
        message.normalized_message_id or ""
    )
    if clarification:
        return RequestRepository(session).get(clarification.request_id)
    outbox = session.scalar(
        select(OutboxMessage).where(OutboxMessage.outbound_email_id == message.id)
    )
    if outbox and outbox.related_request_id:
        return RequestRepository(session).get(outbox.related_request_id)
    initiated_request = session.scalar(
        select(BusinessRequest).where(BusinessRequest.initiating_email_id == message.id)
    )
    if initiated_request:
        return initiated_request
    if message.conversation_id:
        open_requests = RequestRepository(session).open_for_conversation(message.conversation_id)
        if len(open_requests) == 1:
            return open_requests[0]
        all_requests = list(
            session.scalars(
                select(BusinessRequest).where(
                    BusinessRequest.conversation_id == message.conversation_id
                )
            )
        )
        if len(all_requests) == 1:
            return all_requests[0]
    return None


def correlate_email(session: Session, parsed: ParsedEmail) -> CorrelationResult:
    emails = EmailRepository(session)
    requests = RequestRepository(session)
    conversations = ConversationRepository(session)

    direct_ids = [parsed.in_reply_to] if parsed.in_reply_to else []
    reference_ids = list(parsed.references)
    direct_matches = emails.by_any_rfc_id(direct_ids)
    reference_matches = emails.by_any_rfc_id(reference_ids)
    header_ids = direct_ids if direct_matches else reference_ids
    header_matches = direct_matches if direct_matches else reference_matches
    header_conversations = {
        message.conversation_id for message in header_matches if message.conversation_id is not None
    }
    header_requests = {
        request.id
        for message in header_matches
        if (request := _request_for_message(session, message)) is not None
    }
    clarification_ids = {
        clarification.id
        for identifier in header_ids
        if (clarification := ClarificationRepository(session).by_outbound_rfc_id(identifier))
        is not None
    }

    visible_refs = parse_request_references(
        parsed.subject, parsed.segmentation.latest_message_candidate
    )
    marker_requests = [
        request for ref in visible_refs if (request := requests.by_public_reference(ref))
    ]
    marker_request_ids = {request.id for request in marker_requests}
    marker_conversations = {request.conversation_id for request in marker_requests}

    conflicts: list[str] = []
    if len(header_conversations) > 1:
        conflicts.append("headers_reference_multiple_conversations")
    if len(header_requests) > 1:
        conflicts.append("headers_reference_multiple_requests")
    if len(marker_request_ids) > 1:
        conflicts.append("multiple_visible_request_markers")
    if header_requests and marker_request_ids and header_requests != marker_request_ids:
        conflicts.append("header_marker_request_conflict")
    if (
        header_conversations
        and marker_conversations
        and header_conversations != marker_conversations
    ):
        conflicts.append("header_marker_conversation_conflict")
    if marker_requests and any(
        request.conversation.primary_sender
        and request.conversation.primary_sender.casefold() != parsed.sender_address
        for request in marker_requests
    ):
        conflicts.append("marker_sender_mismatch")
    if header_matches and any(
        message.conversation
        and message.conversation.primary_sender
        and message.conversation.primary_sender.casefold() != parsed.sender_address
        for message in header_matches
    ):
        conflicts.append("header_sender_mismatch")

    if conflicts:
        return CorrelationResult(
            conversation_id=str(next(iter(header_conversations or marker_conversations), ""))
            or None,
            request_id=str(next(iter(header_requests or marker_request_ids), "")) or None,
            clarification_id=str(next(iter(clarification_ids), "")) or None,
            strength=CorrelationStrength.CONFLICT,
            matched_by="conflicting_signals",
            conflicts=conflicts,
        )

    if header_matches:
        conversation_id = next(iter(header_conversations), None)
        request_id = next(iter(header_requests), None)
        return CorrelationResult(
            conversation_id=str(conversation_id) if conversation_id else None,
            request_id=str(request_id) if request_id else None,
            clarification_id=(str(next(iter(clarification_ids))) if clarification_ids else None),
            strength=CorrelationStrength.STRONG,
            matched_by="in_reply_to" if direct_matches else "references",
        )

    if marker_requests:
        request = marker_requests[0]
        return CorrelationResult(
            conversation_id=str(request.conversation_id),
            request_id=str(request.id),
            clarification_id=None,
            strength=CorrelationStrength.STRONG,
            matched_by="visible_request_marker",
        )

    subject_candidates = conversations.subject_candidates(
        parsed.normalized_subject, parsed.sender_address
    )
    if len(subject_candidates) == 1:
        candidate = subject_candidates[0]
        open_requests = requests.open_for_conversation(candidate.id)
        return CorrelationResult(
            conversation_id=str(candidate.id),
            request_id=str(open_requests[0].id) if len(open_requests) == 1 else None,
            clarification_id=None,
            strength=CorrelationStrength.WEAK,
            matched_by="normalized_subject",
            conflicts=["multiple_open_requests"] if len(open_requests) > 1 else [],
        )
    if len(subject_candidates) > 1:
        return CorrelationResult(
            conversation_id=None,
            request_id=None,
            clarification_id=None,
            strength=CorrelationStrength.WEAK,
            matched_by="normalized_subject",
            conflicts=["subject_matches_multiple_conversations"],
        )
    return CorrelationResult(
        None, None, None, CorrelationStrength.NEW, matched_by="new_conversation"
    )


def uuid_or_none(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None
