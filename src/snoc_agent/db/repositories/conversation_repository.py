from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from snoc_agent.db.models import Conversation


class ConversationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, conversation: Conversation) -> Conversation:
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def get(self, conversation_id: uuid.UUID) -> Conversation | None:
        return self.session.get(Conversation, conversation_id)

    def subject_candidates(self, normalized_subject: str, sender: str) -> list[Conversation]:
        if not normalized_subject:
            return []
        return list(
            self.session.scalars(
                select(Conversation)
                .where(
                    Conversation.normalized_subject == normalized_subject,
                    Conversation.primary_sender == sender,
                )
                .order_by(Conversation.last_message_at.desc())
            )
        )
