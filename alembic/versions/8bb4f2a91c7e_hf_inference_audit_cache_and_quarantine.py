"""HF inference audit, persistent cache, evaluation runs, and quarantine.

Revision ID: 8bb4f2a91c7e
Revises: 13d76d83d129
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8bb4f2a91c7e"
down_revision: str | None = "13d76d83d129"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("email_messages") as batch:
        batch.add_column(sa.Column("raw_size_bytes", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("quarantine_category", sa.String(100), nullable=True))
        batch.add_column(sa.Column("quarantine_message", sa.Text(), nullable=True))
        batch.add_column(sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column(
                "quarantine_retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False
            )
        )
        batch.add_column(
            sa.Column(
                "context_limit_metadata", sa.JSON(), server_default=sa.text("'{}'"), nullable=False
            )
        )
        batch.create_index("ix_email_messages_quarantine_category", ["quarantine_category"])

    model_columns = (
        sa.Column("base_model_id", sa.String(300), nullable=True),
        sa.Column("resolved_model_id", sa.String(400), nullable=True),
        sa.Column("requested_route", sa.String(400), nullable=True),
        sa.Column("reported_provider", sa.String(150), nullable=True),
        sa.Column("provider_request_id", sa.String(300), nullable=True),
        sa.Column("structured_output_mode", sa.String(40), nullable=True),
        sa.Column("schema_name", sa.String(200), nullable=True),
        sa.Column("json_schema", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("schema_hash", sa.String(64), nullable=True),
        sa.Column("fallback_reason", sa.Text(), nullable=True),
        sa.Column("parse_attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("validation_errors", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("reasoning_output", sa.Text(), nullable=True),
        sa.Column(
            "request_attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("pricing_metadata", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("input_cost_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("output_cost_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("total_cost_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("cost_basis", sa.String(40), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("generation_settings", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("generation_settings_hash", sa.String(64), nullable=True),
        sa.Column("logprob_metrics", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("error_category", sa.String(80), nullable=True),
        sa.Column(
            "cached_from_model_run_id",
            sa.Uuid(),
            sa.ForeignKey("model_runs.id"),
            nullable=True,
        ),
    )
    with op.batch_alter_table("model_runs") as batch:
        for column in model_columns:
            batch.add_column(column)
        for name, columns in (
            ("ix_model_runs_base_model_id", ["base_model_id"]),
            ("ix_model_runs_resolved_model_id", ["resolved_model_id"]),
            ("ix_model_runs_reported_provider", ["reported_provider"]),
            ("ix_model_runs_schema_hash", ["schema_hash"]),
            ("ix_model_runs_generation_settings_hash", ["generation_settings_hash"]),
            ("ix_model_runs_error_category", ["error_category"]),
        ):
            batch.create_index(name, columns)

    op.create_table(
        "inference_cache_entries",
        sa.Column("cache_key", sa.String(64), primary_key=True),
        sa.Column("model_run_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("base_model_id", sa.String(300), nullable=False),
        sa.Column("resolved_model_id", sa.String(400), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("structured_output_mode", sa.String(40), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("schema_hash", sa.String(64), nullable=False),
        sa.Column("generation_settings_hash", sa.String(64), nullable=False),
        sa.Column("hit_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["model_run_id"], ["model_runs.id"]),
    )
    op.create_index(
        "ix_inference_cache_entries_model_run_id", "inference_cache_entries", ["model_run_id"]
    )
    op.create_index("ix_inference_cache_entries_stage", "inference_cache_entries", ["stage"])

    op.create_table(
        "evaluation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("dataset_path", sa.Text(), nullable=False),
        sa.Column("dataset_hash", sa.String(64), nullable=False),
        sa.Column("dataset_split", sa.String(40), nullable=True),
        sa.Column("configuration_hash", sa.String(64), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("output_dir", sa.Text(), nullable=False),
        sa.Column("budget_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("stop_before_budget_usd", sa.Numeric(18, 9), nullable=True),
        sa.Column("cost_so_far_usd", sa.Numeric(18, 9), nullable=False),
        sa.Column("budget_status", sa.String(50), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("unknown_cost_request_count", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_remaining_calls", sa.Integer(), nullable=True),
        sa.Column("checkpoint_row", sa.Integer(), nullable=False),
        sa.Column("resumable_command", sa.Text(), nullable=True),
        sa.Column("final_error_category", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_evaluation_runs_status", "evaluation_runs", ["status"])
    op.create_index("ix_evaluation_runs_dataset_hash", "evaluation_runs", ["dataset_hash"])
    op.create_index(
        "ix_evaluation_runs_configuration_hash", "evaluation_runs", ["configuration_hash"]
    )

    op.create_table(
        "evaluation_inferences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("evaluation_run_id", sa.Uuid(), nullable=False),
        sa.Column("example_id", sa.String(300), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("analyzer_source_model_id", sa.String(300), nullable=True),
        sa.Column("base_model_id", sa.String(300), nullable=False),
        sa.Column("proposal_hash", sa.String(64), nullable=False),
        sa.Column("inference_key", sa.String(64), nullable=True),
        sa.Column("model_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "attempt_model_run_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("error_category", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["evaluation_run_id"], ["evaluation_runs.id"]),
        sa.ForeignKeyConstraint(["model_run_id"], ["model_runs.id"]),
        sa.UniqueConstraint(
            "evaluation_run_id",
            "example_id",
            "stage",
            "base_model_id",
            "proposal_hash",
            name="uq_evaluation_inference_identity",
        ),
    )
    for name, columns in (
        ("ix_evaluation_inferences_evaluation_run_id", ["evaluation_run_id"]),
        ("ix_evaluation_inferences_example_id", ["example_id"]),
        ("ix_evaluation_inferences_stage", ["stage"]),
        ("ix_evaluation_inferences_model_run_id", ["model_run_id"]),
        ("ix_evaluation_inferences_inference_key", ["inference_key"]),
        ("ix_evaluation_inferences_status", ["status"]),
    ):
        op.create_index(name, "evaluation_inferences", columns)

    op.create_table(
        "calibration_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("dataset_hash", sa.String(64), nullable=False),
        sa.Column("dataset_split", sa.String(40), nullable=False),
        sa.Column("feature_version", sa.String(100), nullable=False),
        sa.Column("policy_version", sa.String(100), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_calibration_artifacts_dataset_hash", "calibration_artifacts", ["dataset_hash"]
    )


def downgrade() -> None:
    op.drop_table("calibration_artifacts")
    op.drop_table("evaluation_inferences")
    op.drop_table("evaluation_runs")
    op.drop_table("inference_cache_entries")

    with op.batch_alter_table("model_runs") as batch:
        for name in (
            "ix_model_runs_error_category",
            "ix_model_runs_generation_settings_hash",
            "ix_model_runs_schema_hash",
            "ix_model_runs_reported_provider",
            "ix_model_runs_resolved_model_id",
            "ix_model_runs_base_model_id",
        ):
            batch.drop_index(name)
        for name in (
            "cached_from_model_run_id",
            "error_category",
            "logprob_metrics",
            "generation_settings_hash",
            "generation_settings",
            "cost_basis",
            "total_cost_usd",
            "output_cost_usd",
            "input_cost_usd",
            "pricing_metadata",
            "total_tokens",
            "reasoning_output",
            "request_attempt_count",
            "validation_errors",
            "parse_attempt_count",
            "fallback_reason",
            "schema_hash",
            "json_schema",
            "schema_name",
            "structured_output_mode",
            "provider_request_id",
            "reported_provider",
            "requested_route",
            "resolved_model_id",
            "base_model_id",
        ):
            batch.drop_column(name)

    with op.batch_alter_table("email_messages") as batch:
        batch.drop_index("ix_email_messages_quarantine_category")
        for name in (
            "context_limit_metadata",
            "quarantine_retry_count",
            "quarantined_at",
            "quarantine_message",
            "quarantine_category",
            "raw_size_bytes",
        ):
            batch.drop_column(name)
