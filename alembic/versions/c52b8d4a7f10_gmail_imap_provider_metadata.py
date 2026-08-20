"""Persist optional provider-specific IMAP metadata.

Revision ID: c52b8d4a7f10
Revises: 8bb4f2a91c7e
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c52b8d4a7f10"
down_revision: str | None = "8bb4f2a91c7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("email_messages") as batch:
        batch.add_column(
            sa.Column(
                "provider_metadata_json",
                sa.JSON(),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("email_messages") as batch:
        batch.drop_column("provider_metadata_json")
