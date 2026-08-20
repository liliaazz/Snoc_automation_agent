"""Conversation history retrieval service for context-aware analysis.

Provides ordered message history and summarization for the ContextBuilder,
enabling agents to reference prior messages in the same conversation thread.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from snoc_agent.db.models import Conversation, EmailMessage
from snoc_agent.db.session import SessionFactory, session_scope

logger = logging.getLogger(__name__)


@dataclass
class ConversationMessage:
    """Simplified representation of a historical message."""

    id: str
    sender: str
    direction: str
    subject: str
    latest_user_message: str
    created_at: str | None
    processing_status: str


@dataclass
class ConversationHistory:
    """Full conversation history for a given conversation."""

    conversation_id: str
    messages: list[ConversationMessage]
    summary: str | None = None
    root_subject: str = ""
    primary_sender: str = ""
    message_count: int = 0


class ConversationHistoryService:
    """Retrieves and summarizes conversation history from the database."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self.session_factory = session_factory

    def get_history(
        self,
        conversation_id: uuid.UUID,
        *,
        exclude_email_id: uuid.UUID | None = None,
        limit: int = 20,
    ) -> ConversationHistory | None:
        """Retrieve the ordered message history for a conversation.

        Args:
            conversation_id: The conversation to fetch history for.
            exclude_email_id: If provided, exclude this email (the current message).
            limit: Maximum number of historical messages to return.
        """
        with session_scope(self.session_factory) as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                return None

            stmt = (
                select(EmailMessage)
                .where(EmailMessage.conversation_id == conversation_id)
                .order_by(EmailMessage.created_at)
            )
            emails = list(session.scalars(stmt).all())

            messages: list[ConversationMessage] = []
            for email in emails:
                if exclude_email_id and email.id == exclude_email_id:
                    continue
                messages.append(
                    ConversationMessage(
                        id=str(email.id),
                        sender=email.sender or "",
                        direction=email.direction or "inbound",
                        subject=email.subject or "",
                        latest_user_message=email.latest_user_message or "",
                        created_at=email.created_at.isoformat() if email.created_at else None,
                        processing_status=email.processing_status or "",
                    )
                )

            # Take the most recent messages (up to limit)
            if len(messages) > limit:
                messages = messages[-limit:]

            summary = self._build_text_summary(messages)

            return ConversationHistory(
                conversation_id=str(conversation_id),
                messages=messages,
                summary=summary,
                root_subject=conversation.normalized_subject or "",
                primary_sender=conversation.primary_sender or "",
                message_count=len(messages),
            )

    def get_history_for_email(
        self,
        email_id: uuid.UUID,
        *,
        limit: int = 20,
    ) -> ConversationHistory | None:
        """Retrieve conversation history given a specific email ID."""
        with session_scope(self.session_factory) as session:
            email = session.get(EmailMessage, email_id)
            if email is None or email.conversation_id is None:
                return None
            return self.get_history(
                email.conversation_id,
                exclude_email_id=email_id,
                limit=limit,
            )

    def _build_text_summary(self, messages: list[ConversationMessage]) -> str | None:
        """Build a simple text summary of the conversation history.

        For production use, this could be replaced with an LLM-based summarizer
        that produces more concise and insightful summaries.
        """
        if not messages:
            return None

        parts: list[str] = []
        for msg in messages:
            direction_label = "User" if msg.direction == "inbound" else "Agent"
            body_preview = (
                msg.latest_user_message[:200] if msg.latest_user_message else "(no content)"
            )
            parts.append(f"[{direction_label}] {msg.sender}: {body_preview}")

        return "\n".join(parts)
