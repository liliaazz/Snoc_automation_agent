"""Add locally authenticated dashboard users.

Revision ID: d6810ae247c3
Revises: b71c9e4d2a80
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d6810ae247c3"
down_revision: str | None = "b71c9e4d2a80"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dashboard_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("normalized_username", sa.String(100), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("normalized_username"),
    )
    op.create_index(
        "ix_dashboard_users_normalized_username",
        "dashboard_users",
        ["normalized_username"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_dashboard_users_normalized_username", table_name="dashboard_users")
    op.drop_table("dashboard_users")
