"""Add a durable correction-grace execution queue.

Revision ID: b71c9e4d2a80
Revises: a92e710c4b35
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b71c9e4d2a80"
down_revision: str | None = "a92e710c4b35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduled_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("operation_revision", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("source_email_id", sa.Uuid(), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("cancellation_source_email_id", sa.Uuid(), nullable=True),
        sa.Column(
            "cancellation_data",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_id", sa.Uuid(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('scheduled', 'dispatching', 'dispatched', 'cancelled', 'failed')",
            name=op.f("ck_scheduled_executions_scheduled_execution_status"),
        ),
        sa.ForeignKeyConstraint(
            ["cancellation_source_email_id"],
            ["email_messages.id"],
            name=op.f("fk_scheduled_executions_cancellation_source_email_id_email_messages"),
        ),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["executions.id"],
            name=op.f("fk_scheduled_executions_execution_id_executions"),
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
            name=op.f("fk_scheduled_executions_operation_id_operations"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["requests.id"],
            name=op.f("fk_scheduled_executions_request_id_requests"),
        ),
        sa.ForeignKeyConstraint(
            ["source_email_id"],
            ["email_messages.id"],
            name=op.f("fk_scheduled_executions_source_email_id_email_messages"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_scheduled_executions")),
        sa.UniqueConstraint(
            "execution_id",
            name=op.f("uq_scheduled_executions_execution_id"),
        ),
        sa.UniqueConstraint(
            "operation_id",
            "operation_revision",
            name="uq_scheduled_execution_operation_revision",
        ),
    )
    op.create_index(
        op.f("ix_scheduled_executions_idempotency_key"),
        "scheduled_executions",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_scheduled_executions_not_before"),
        "scheduled_executions",
        ["not_before"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_executions_operation_id"),
        "scheduled_executions",
        ["operation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_executions_request_id"),
        "scheduled_executions",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_executions_source_email_id"),
        "scheduled_executions",
        ["source_email_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_scheduled_executions_status"),
        "scheduled_executions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_scheduled_executions_status_not_before",
        "scheduled_executions",
        ["status", "not_before"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_scheduled_executions_status_not_before",
        table_name="scheduled_executions",
    )
    op.drop_index(
        op.f("ix_scheduled_executions_status"),
        table_name="scheduled_executions",
    )
    op.drop_index(
        op.f("ix_scheduled_executions_source_email_id"),
        table_name="scheduled_executions",
    )
    op.drop_index(
        op.f("ix_scheduled_executions_request_id"),
        table_name="scheduled_executions",
    )
    op.drop_index(
        op.f("ix_scheduled_executions_operation_id"),
        table_name="scheduled_executions",
    )
    op.drop_index(
        op.f("ix_scheduled_executions_not_before"),
        table_name="scheduled_executions",
    )
    op.drop_index(
        op.f("ix_scheduled_executions_idempotency_key"),
        table_name="scheduled_executions",
    )
    op.drop_table("scheduled_executions")
