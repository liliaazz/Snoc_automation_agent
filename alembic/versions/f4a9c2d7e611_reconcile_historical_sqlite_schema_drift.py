"""Reconcile historical SQLite databases with the declared HF audit schema.

Fresh databases already receive these objects from revision 8bb4f2a91c7e. Some retained SQLite
files were stamped at that revision during an earlier schema reconciliation without all objects
being present. This migration is intentionally conditional so both histories converge.

Revision ID: f4a9c2d7e611
Revises: c52b8d4a7f10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f4a9c2d7e611"
down_revision: str | None = "c52b8d4a7f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    evaluation_columns = {
        column["name"] for column in inspector.get_columns("evaluation_inferences")
    }
    evaluation_indexes = {index["name"] for index in inspector.get_indexes("evaluation_inferences")}
    with op.batch_alter_table("evaluation_inferences") as batch:
        if "inference_key" not in evaluation_columns:
            batch.add_column(sa.Column("inference_key", sa.String(64), nullable=True))
        if "attempt_model_run_ids" not in evaluation_columns:
            batch.add_column(
                sa.Column(
                    "attempt_model_run_ids",
                    sa.JSON(),
                    server_default=sa.text("'[]'"),
                    nullable=False,
                )
            )
        if "ix_evaluation_inferences_inference_key" not in evaluation_indexes:
            batch.create_index(
                "ix_evaluation_inferences_inference_key",
                ["inference_key"],
                unique=False,
            )

    inspector = sa.inspect(bind)
    model_run_foreign_keys = inspector.get_foreign_keys("model_runs")
    cached_run_fk_exists = any(
        foreign_key.get("constrained_columns") == ["cached_from_model_run_id"]
        and foreign_key.get("referred_table") == "model_runs"
        and foreign_key.get("referred_columns") == ["id"]
        for foreign_key in model_run_foreign_keys
    )
    if not cached_run_fk_exists:
        with op.batch_alter_table("model_runs") as batch:
            batch.create_foreign_key(
                "fk_model_runs_cached_from_model_run_id_model_runs",
                "model_runs",
                ["cached_from_model_run_id"],
                ["id"],
            )


def downgrade() -> None:
    # This is a convergence/data-repair migration. The repaired objects belong to the schema
    # introduced by 8bb4f2a91c7e, so removing them would make that earlier declared schema invalid.
    pass
