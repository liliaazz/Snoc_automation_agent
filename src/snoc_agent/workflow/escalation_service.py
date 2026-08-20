"""Structured human-escalation persistence."""

from __future__ import annotations

import hashlib
from email import policy
from email.message import EmailMessage as MIMEEmailMessage
from email.parser import BytesParser
from email.utils import make_msgid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from snoc_agent.db.models import (
    BusinessRequest,
    EmailMessage,
    Escalation,
    OutboxMessage,
)
from snoc_agent.domain.enums import Direction, OutboxStatus, ProcessingStatus
from snoc_agent.domain.value_objects import reject_header_injection
from snoc_agent.mail.headers import build_references, normalize_message_id
from snoc_agent.mail.mime import extract_content


def _original_message(email: EmailMessage) -> MIMEEmailMessage:
    if email.raw_eml_blob is not None:
        raw_message = email.raw_eml_blob
    elif email.raw_eml_path:
        raw_message = Path(email.raw_eml_path).read_bytes()
    else:
        fallback = MIMEEmailMessage()
        fallback["From"] = email.sender
        fallback["To"] = ", ".join(email.recipients_json)
        fallback["Subject"] = email.subject
        if email.rfc_message_id:
            fallback["Message-ID"] = email.rfc_message_id
        fallback.set_content(email.raw_text or email.latest_user_message)
        return fallback
    parsed = BytesParser(policy=policy.default).parsebytes(raw_message)
    if not isinstance(parsed, MIMEEmailMessage):
        raise TypeError("stored original email could not be parsed for forwarding")
    return parsed


def _inline_forward_body(
    email: EmailMessage,
    original: MIMEEmailMessage,
    *,
    reference: str,
    reason_code: str,
    summary: str,
) -> str:
    """Build a normal inline forward containing the complete visible message chain."""

    original_text, _, _, _ = extract_content(original)
    original_text = (
        original_text.strip() or email.raw_text.strip() or email.latest_user_message.strip()
    )
    sender = str(original.get("From") or email.sender).strip()
    recipients = str(original.get("To") or ", ".join(email.recipients_json)).strip()
    cc = str(original.get("Cc") or ", ".join(email.cc_json)).strip()
    subject = str(original.get("Subject") or email.subject or "(sans objet)").strip()
    date = str(original.get("Date") or "").strip()

    forwarded_headers = [f"De : {sender}"]
    if date:
        forwarded_headers.append(f"Date : {date}")
    forwarded_headers.extend([f"Objet : {subject}", f"À : {recipients}"])
    if cc:
        forwarded_headers.append(f"Cc : {cc}")

    return "\n".join(
        [
            "Ce message client a été transféré par le service SNOC pour traitement humain.",
            "",
            f"Référence : {reference}",
            f"Motif : {reason_code}",
            f"Résumé : {summary}",
            "",
            "---------- Message transféré ----------",
            *forwarded_headers,
            "",
            original_text,
        ]
    )


def create_escalation(
    session: Session,
    *,
    email: EmailMessage,
    request: BusinessRequest | None,
    recipient: str,
    reason_code: str,
    summary: str,
    evidence: dict[str, Any],
    queue_email: bool = False,
    sender_address: str | None = None,
) -> Escalation:
    latest_text = email.latest_user_message[:4000]
    stored_operations = []
    if request:
        stored_operations = [
            {
                "operation_id": str(operation.id),
                "action": operation.action,
                "status": operation.status,
                "pdv_code": operation.pdv_code,
                "phone": operation.phone,
                "additional_payload": operation.additional_payload,
                "missing_fields": operation.missing_fields,
                "evidence": operation.evidence,
                "field_provenance": operation.field_provenance,
                "contradiction_data": operation.contradiction_data,
                "current_revision": operation.current_revision,
                "final_decision": operation.final_decision,
            }
            for operation in request.operations
        ]
    structured_evidence = {
        **evidence,
        "email_context": {
            "internal_email_id": str(email.id),
            "sender": email.sender,
            "subject": email.subject,
            "message_id": email.rfc_message_id,
            "in_reply_to": email.in_reply_to,
            "references": email.references_json,
            "latest_user_message": latest_text,
            "latest_user_message_truncated": len(email.latest_user_message) > len(latest_text),
            "raw_eml_path": email.raw_eml_path,
        },
        "stored_request_state": {
            "request_id": str(request.id) if request else None,
            "public_reference": request.public_reference if request else None,
            "request_status": request.status if request else None,
            "operations": stored_operations,
        },
    }
    escalation = Escalation(
        request_id=request.id if request else None,
        email_message_id=email.id,
        recipient=recipient,
        reason_code=reason_code,
        summary=summary,
        evidence=structured_evidence,
    )
    session.add(escalation)
    session.flush()
    if request:
        request.escalation_reason = summary
    if queue_email:
        if not sender_address:
            raise ValueError("sender_address is required when queuing an escalation email")
        sender_address = reject_header_injection(sender_address)
        recipient = reject_header_injection(recipient)
        reference = request.public_reference if request else "SNOC-NON-CORRELATED"
        original_subject = email.subject.strip() or "(sans objet)"
        subject = reject_header_injection(f"Fwd: {original_subject}")
        original = _original_message(email)
        body = _inline_forward_body(
            email,
            original,
            reference=reference,
            reason_code=reason_code,
            summary=summary,
        )
        domain = sender_address.rsplit("@", 1)[-1] if "@" in sender_address else None
        message_id = make_msgid(domain=domain)
        references = build_references(email.references_json, email.rfc_message_id)
        headers = {
            "Message-ID": message_id,
            "In-Reply-To": normalize_message_id(email.rfc_message_id) or "",
            "References": " ".join(references),
            "X-SNOC-Agent-Generated": "true",
            "X-SNOC-Escalation-ID": str(escalation.id),
        }
        if request:
            headers["X-SNOC-Request-ID"] = request.public_reference
        forwarded = MIMEEmailMessage(policy=policy.SMTP)
        forwarded["From"] = sender_address
        forwarded["To"] = recipient
        forwarded["Subject"] = subject
        for name, value in headers.items():
            if value:
                forwarded[name] = value
        forwarded.set_content(body)
        raw_forward = forwarded.as_bytes()
        outbound = EmailMessage(
            conversation_id=email.conversation_id,
            direction=Direction.OUTBOUND.value,
            rfc_message_id=message_id,
            normalized_message_id=normalize_message_id(message_id),
            in_reply_to=headers["In-Reply-To"] or None,
            references_json=references,
            sender=sender_address,
            recipients_json=[recipient],
            cc_json=[],
            subject=subject,
            normalized_subject=email.normalized_subject,
            raw_text=body,
            latest_user_message=body,
            quoted_text="",
            signature_text="",
            raw_eml_blob=raw_forward,
            raw_size_bytes=len(raw_forward),
            raw_sha256=hashlib.sha256(raw_forward).hexdigest(),
            mime_type=forwarded.get_content_type(),
            attachment_metadata=[],
            flags_json=[],
            processing_status=ProcessingStatus.STORED.value,
            parsing_warnings=[],
            correlation_details={},
        )
        session.add(outbound)
        session.flush()
        session.add(
            OutboxMessage(
                related_request_id=request.id if request else None,
                outbound_email_id=outbound.id,
                recipient=recipient,
                subject=subject,
                body=body,
                headers=headers,
                status=OutboxStatus.PENDING.value,
            )
        )
    return escalation
