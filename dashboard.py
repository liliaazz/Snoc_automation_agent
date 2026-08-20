"""Read-only Streamlit audit dashboard for the SNOC workflow database."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from snoc_agent.config import Settings


def _database_url() -> str:
    return os.environ.get("SNOC_DASHBOARD_DATABASE_URL") or Settings().database_url


@st.cache_resource
def _engine(database_url: str) -> Engine:
    return create_engine(database_url)


def _rows(engine: Engine, query: str, parameters: Mapping[str, Any] | None = None) -> pd.DataFrame:
    with engine.connect() as connection:
        return pd.read_sql_query(text(query), connection, params=dict(parameters or {}))


def _json_value(value: object) -> None:
    if value in (None, "", [], {}):
        st.caption("No data recorded.")
    else:
        st.json(value, expanded=False)


st.set_page_config(page_title="SNOC Workflow Audit", layout="wide", page_icon="🕵️")
st.title("SNOC workflow audit")
st.caption("Read-only view of email, model, policy, clarification, and execution records.")

database_url = _database_url()
safe_url = make_url(database_url).render_as_string(hide_password=True)
st.sidebar.header("Data source")
st.sidebar.code(safe_url)
if st.sidebar.button("Refresh"):
    st.cache_data.clear()
    st.rerun()

try:
    engine = _engine(database_url)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
except Exception as exc:  # pragma: no cover - rendered for operators
    st.error(f"Database connection failed: {type(exc).__name__}: {exc}")
    st.stop()

emails = _rows(
    engine,
    """
    SELECT id, conversation_id, created_at, sender, subject, processing_status,
           automated_classification, authorization_allowed, imap_uid, uidvalidity,
           rfc_message_id
    FROM email_messages
    WHERE direction = 'inbound'
    ORDER BY created_at DESC
    LIMIT 200
    """,
)

model_summary = _rows(
    engine,
    """
    SELECT COUNT(*) AS calls,
           COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
           COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
           COALESCE(SUM(total_cost_usd), 0) AS known_cost_usd,
           SUM(CASE WHEN structured_output_valid THEN 0 ELSE 1 END) AS invalid_outputs
    FROM model_runs
    """,
).iloc[0]
request_summary = _rows(
    engine,
    """
    SELECT COUNT(*) AS requests,
           SUM(CASE WHEN UPPER(status) = 'COMPLETED' THEN 1 ELSE 0 END) AS completed,
           SUM(CASE WHEN UPPER(status) = 'ESCALATED' THEN 1 ELSE 0 END) AS escalated
    FROM requests
    """,
).iloc[0]
workflow_summary = _rows(
    engine,
    """
    SELECT COUNT(*) AS runs,
           SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
           SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
    FROM workflow_runs
    """,
).iloc[0]

metric_columns = st.columns(7)
metric_columns[0].metric("Inbound emails", len(emails))
metric_columns[1].metric("Requests", int(request_summary["requests"] or 0))
metric_columns[2].metric("Completed", int(request_summary["completed"] or 0))
metric_columns[3].metric("Escalated", int(request_summary["escalated"] or 0))
metric_columns[4].metric("Model calls", int(model_summary["calls"] or 0))
metric_columns[5].metric("Invalid model outputs", int(model_summary["invalid_outputs"] or 0))
metric_columns[6].metric(
    "Graph runs",
    f"{int(workflow_summary['completed'] or 0)}/{int(workflow_summary['runs'] or 0)}",
    delta=(
        f"{int(workflow_summary['failed'] or 0)} failed"
        if int(workflow_summary["failed"] or 0)
        else None
    ),
    delta_color="inverse",
)

journey_report_path = Path(
    os.environ.get("SNOC_JOURNEY_REPORT", "outputs/docker_mail_journey/report.json")
)
if journey_report_path.exists():
    try:
        journey_report = json.loads(journey_report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        st.warning(f"Journey report could not be read: {type(exc).__name__}")
    else:
        with st.expander("Latest production-style mailbox journey", expanded=True):
            totals = journey_report.get("totals", {})
            cols = st.columns(4)
            cols[0].metric("Journey scenarios", totals.get("scenarios", 0))
            cols[1].metric("Passed", totals.get("passed", 0))
            cols[2].metric("Failed", totals.get("failed", 0))
            cols[3].metric(
                "Single Gmail thread",
                "yes" if totals.get("threading_failures", 0) == 0 else "no",
            )
            journey_rows = [
                {
                    "scenario": row.get("scenario"),
                    "subject": row.get("subject"),
                    "passed": row.get("passed"),
                    "terminal_state": row.get("terminal_state"),
                    "gmail_thread_id": row.get("gmail_thread_id"),
                    "threading_ok": row.get("threading_ok"),
                    "quality_failures": row.get("quality_failures"),
                }
                for row in journey_report.get("scenarios", [])
            ]
            st.dataframe(journey_rows, hide_index=True, width="stretch")

st.subheader("Treated emails")
if emails.empty:
    st.info("No inbound email has been persisted in this database yet.")
    st.stop()

display_columns = [
    "created_at",
    "sender",
    "subject",
    "processing_status",
    "automated_classification",
    "authorization_allowed",
    "imap_uid",
]
st.dataframe(emails[display_columns], hide_index=True, width="stretch")

labels = {
    str(row["id"]): f"{row['created_at']} · {row['subject']} · {row['processing_status']}"
    for _, row in emails.iterrows()
}
selected_id = st.selectbox(
    "Inspect every stage for one email",
    options=list(labels),
    format_func=labels.__getitem__,
)
selected = emails.loc[emails["id"].astype(str) == selected_id].iloc[0]

email_tab, agent_tab, model_tab, policy_tab, state_tab, delivery_tab = st.tabs(
    [
        "Email + IMAP",
        "Agent trace",
        "Models",
        "Safety decision",
        "Request state",
        "Replies + execution",
    ]
)

with email_tab:
    left, right = st.columns(2)
    with left:
        st.markdown("#### Envelope and mailbox metadata")
        _json_value(
            {
                "id": selected_id,
                "message_id": selected["rfc_message_id"],
                "sender": selected["sender"],
                "subject": selected["subject"],
                "processing_status": selected["processing_status"],
                "imap_uid": selected["imap_uid"],
                "uidvalidity": selected["uidvalidity"],
            }
        )
    with right:
        email_detail = _rows(
            engine,
            """
            SELECT recipients_json, cc_json, in_reply_to, references_json, flags_json,
                   provider_metadata_json, internal_date, message_date, parsing_warnings,
                   context_limit_metadata,
                   authorization_allowed, authorization_reason, correlation_details,
                   latest_user_message, quoted_text, quarantine_category, quarantine_message
            FROM email_messages WHERE CAST(id AS TEXT) = :email_id
            """,
            {"email_id": selected_id},
        ).iloc[0]
        st.markdown("#### Parsing, authorization, and correlation")
        _json_value(email_detail.to_dict())

with agent_tab:
    workflow_runs = _rows(
        engine,
        """
        SELECT id, graph_version, engine, status, current_agent, started_at,
               completed_at, error_category, error_message
        FROM workflow_runs
        WHERE CAST(inbound_email_id AS TEXT) = :email_id
        ORDER BY started_at
        """,
        {"email_id": selected_id},
    )
    if workflow_runs.empty:
        st.info("No LangGraph workflow run was recorded for this email.")
    else:
        st.dataframe(workflow_runs, hide_index=True, width="stretch")
        for _, workflow_run in workflow_runs.iterrows():
            st.markdown(f"#### Run `{workflow_run['id']}`")
            events = _rows(
                engine,
                """
                SELECT sequence, agent, status, started_at, completed_at,
                       input_summary, output_summary, error_category, error_message
                FROM workflow_events
                WHERE CAST(workflow_run_id AS TEXT) = :run_id
                ORDER BY sequence
                """,
                {"run_id": str(workflow_run["id"])},
            )
            st.dataframe(
                events[
                    [
                        "sequence",
                        "agent",
                        "status",
                        "started_at",
                        "completed_at",
                        "error_category",
                    ]
                ],
                hide_index=True,
                width="stretch",
            )
            for _, event in events.iterrows():
                with st.expander(f"{event['sequence']}. {event['agent']} · {event['status']}"):
                    _json_value(
                        {
                            "input_summary": event["input_summary"],
                            "output_summary": event["output_summary"],
                            "error_category": event["error_category"],
                            "error_message": event["error_message"],
                        }
                    )

with model_tab:
    runs = _rows(
        engine,
        """
        SELECT id, created_at, stage, backend, base_model_id, resolved_model_id,
               reported_provider, structured_output_mode, structured_output_valid,
               parse_attempt_count, fallback_reason, error_category, latency_seconds,
               prompt_tokens, completion_tokens, total_tokens, total_cost_usd,
               cost_basis, input_context, parsed_output, reasoning_output,
               validation_errors
        FROM model_runs
        WHERE CAST(email_message_id AS TEXT) = :email_id
        ORDER BY created_at
        """,
        {"email_id": selected_id},
    )
    if runs.empty:
        st.info("This email did not reach model inference.")
    else:
        st.dataframe(
            runs[
                [
                    "stage",
                    "base_model_id",
                    "reported_provider",
                    "structured_output_mode",
                    "structured_output_valid",
                    "prompt_tokens",
                    "completion_tokens",
                    "latency_seconds",
                    "error_category",
                ]
            ],
            hide_index=True,
            width="stretch",
        )
        for _, run in runs.iterrows():
            with st.expander(
                f"{run['stage']} · {run['base_model_id']} · {run['id']}", expanded=False
            ):
                _json_value(run.to_dict())

with policy_tab:
    decisions = _rows(
        engine,
        """
        SELECT vd.id, vd.created_at, vd.operation_id, vd.decision, vd.reasons,
               vd.hard_invariant_results, vd.analyzer_result, vd.verifier_result,
               vd.policy_version
        FROM validation_decisions vd
        JOIN operations op ON op.id = vd.operation_id
        JOIN requests req ON req.id = op.request_id
        WHERE CAST(req.initiating_email_id AS TEXT) = :email_id
           OR vd.operation_id IN (
             SELECT operation_id FROM model_runs
             WHERE CAST(email_message_id AS TEXT) = :email_id
           )
        ORDER BY vd.created_at
        """,
        {"email_id": selected_id},
    )
    if decisions.empty:
        st.info("No safety-policy decision is associated with this email.")
    else:
        for _, decision in decisions.iterrows():
            st.markdown(f"#### {decision['decision']} · operation {decision['operation_id']}")
            _json_value(decision.to_dict())

with state_tab:
    requests = _rows(
        engine,
        """
        SELECT DISTINCT req.id, req.public_reference, req.status, req.request_kind,
               req.escalation_reason, req.version, req.created_at, req.completed_at
        FROM requests req
        LEFT JOIN operations op ON op.request_id = req.id
        LEFT JOIN model_runs mr ON mr.operation_id = op.id
        WHERE CAST(req.initiating_email_id AS TEXT) = :email_id
           OR CAST(mr.email_message_id AS TEXT) = :email_id
        ORDER BY req.created_at
        """,
        {"email_id": selected_id},
    )
    st.dataframe(requests, hide_index=True, width="stretch")
    operations = _rows(
        engine,
        """
        SELECT op.id, op.request_id, op.sequence_number, op.action, op.status,
               op.pdv_code, op.phone, op.missing_fields, op.current_revision,
               op.final_decision, op.field_provenance, op.contradiction_data
        FROM operations op
        JOIN requests req ON req.id = op.request_id
        WHERE CAST(req.initiating_email_id AS TEXT) = :email_id
           OR EXISTS (
             SELECT 1 FROM model_runs mr
             WHERE mr.operation_id = op.id
               AND CAST(mr.email_message_id AS TEXT) = :email_id
           )
        ORDER BY op.request_id, op.sequence_number
        """,
        {"email_id": selected_id},
    )
    st.dataframe(operations, hide_index=True, width="stretch")

with delivery_tab:
    message_chain = _rows(
        engine,
        """
        SELECT created_at, direction, sender, recipients_json, subject, rfc_message_id,
               in_reply_to, references_json, provider_metadata_json, processing_status,
               raw_text
        FROM email_messages
        WHERE conversation_id = :conversation_id
        ORDER BY created_at
        """,
        {"conversation_id": selected["conversation_id"]},
    )
    clarifications = _rows(
        engine,
        """
        SELECT id, request_id, status, requested_fields, target_operation_ids,
               round_number, outbound_email_id, reply_email_id, created_at, resolved_at
        FROM clarifications
        WHERE CAST(source_inbound_email_id AS TEXT) = :email_id
           OR CAST(reply_email_id AS TEXT) = :email_id
        ORDER BY created_at
        """,
        {"email_id": selected_id},
    )
    executions = _rows(
        engine,
        """
        SELECT ex.id, ex.operation_id, ex.status, ex.dry_run, ex.endpoint,
               ex.request_payload, ex.response_status, ex.response_body,
               ex.attempt_count, ex.created_at
        FROM executions ex
        JOIN operations op ON op.id = ex.operation_id
        JOIN requests req ON req.id = op.request_id
        WHERE CAST(req.initiating_email_id AS TEXT) = :email_id
           OR EXISTS (
             SELECT 1 FROM model_runs mr
             WHERE mr.operation_id = op.id
               AND CAST(mr.email_message_id AS TEXT) = :email_id
           )
        ORDER BY ex.created_at
        """,
        {"email_id": selected_id},
    )
    outbox = _rows(
        engine,
        """
        SELECT ob.id, ob.related_request_id, ob.related_clarification_id,
               ob.outbound_email_id, ob.recipient, ob.subject, ob.headers, ob.status,
               ob.retry_count, ob.last_error,
               ob.created_at, ob.sent_at
        FROM outbox_messages ob
        WHERE ob.related_request_id IN (
          SELECT DISTINCT req.id FROM requests req
          LEFT JOIN operations op ON op.request_id = req.id
          LEFT JOIN model_runs mr ON mr.operation_id = op.id
          WHERE CAST(req.initiating_email_id AS TEXT) = :email_id
             OR CAST(mr.email_message_id AS TEXT) = :email_id
        )
        ORDER BY ob.created_at
        """,
        {"email_id": selected_id},
    )
    escalations = _rows(
        engine,
        """
        SELECT id, request_id, reason_code, summary, status, evidence, created_at
        FROM escalations
        WHERE CAST(email_message_id AS TEXT) = :email_id
        ORDER BY created_at
        """,
        {"email_id": selected_id},
    )
    st.markdown("#### Complete RFC message chain")
    st.dataframe(message_chain, hide_index=True, width="stretch")
    st.markdown("#### Clarifications")
    st.dataframe(clarifications, hide_index=True, width="stretch")
    st.markdown("#### Outbox")
    st.dataframe(outbox, hide_index=True, width="stretch")
    st.markdown("#### Simulated or real executions")
    st.dataframe(executions, hide_index=True, width="stretch")
    st.markdown("#### Escalations")
    st.dataframe(escalations, hide_index=True, width="stretch")

st.sidebar.metric("Known model cost (USD)", f"{float(model_summary['known_cost_usd'] or 0):.6f}")
st.sidebar.metric("Prompt tokens", int(model_summary["prompt_tokens"] or 0))
st.sidebar.metric("Completion tokens", int(model_summary["completion_tokens"] or 0))
