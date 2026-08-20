"""Deterministic acceptance criteria for the external mailbox journey."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def journey_quality_failures(
    scenario_name: str,
    audit: Mapping[str, Any],
    replies: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Return human-readable failures for one persisted journey scenario."""
    failures: list[str] = []
    operations = audit["operations"]
    executions = audit["executions"]
    workflow_runs = audit.get("workflow_runs", [])
    workflow_events = audit.get("workflow_events", [])
    graph_audit_present = (
        "emails" in audit or "workflow_runs" in audit or "workflow_events" in audit
    )
    email_ids = {str(email["id"]) for email in audit.get("emails", [])}
    canonical_email_ids: set[str] = set()
    expected_agents = (
        ["ingress", "security"]
        if scenario_name == "automated_out_of_office"
        else ["ingress", "security", "nlu", "policy", "fulfilment"]
    )
    for run in workflow_runs:
        if run["status"] != "completed":
            failures.append(f"workflow run {run['id']} did not complete: {run['status']}")
        events = [event for event in workflow_events if event["workflow_run_id"] == run["id"]]
        agents = [event["agent"] for event in events]
        canonical_route = agents == expected_agents or (
            len(agents) >= 2
            and agents == expected_agents[: len(agents)]
            and events[-1]["status"] == "terminal"
        )
        if canonical_route:
            canonical_email_ids.add(str(run["inbound_email_id"]))
        elif agents != ["ingress"] or not events or events[-1]["status"] != "terminal":
            failures.append(
                f"workflow run {run['id']} agent path was {agents}, expected "
                f"a terminal prefix of {expected_agents} or an ingress-only duplicate route"
            )
        if any(event["status"] not in {"succeeded", "terminal"} for event in events):
            failures.append(f"workflow run {run['id']} contains a failed/incomplete agent event")
        sequences = [event["sequence"] for event in events]
        if sequences != list(range(1, len(events) + 1)):
            failures.append(f"workflow run {run['id']} event sequence is not contiguous")
    if graph_audit_present:
        missing_email_ids = email_ids - canonical_email_ids
        if missing_email_ids:
            failures.append(
                "no canonical completed workflow route for inbound emails: "
                + ", ".join(sorted(missing_email_ids))
            )
    if any(not row["dry_run"] for row in executions):
        failures.append("a telecom execution was not marked dry-run")
    if any(not row["valid"] for row in audit.get("model_runs", [])):
        failures.append("a model run did not produce valid structured output")
    if scenario_name == "incomplete_otp_thread":
        if not audit["clarifications"]:
            failures.append("no clarification was recorded")
        if len(audit["emails"]) < 2:
            failures.append("same-thread personal reply was not processed")
        if not replies:
            failures.append("no agent email reached the personal inbox")
    elif scenario_name == "complete_unblock":
        if not any(row["action"] == "account_unblock" for row in operations):
            failures.append("account-unblock operation was not identified")
        if len(executions) != 1:
            failures.append(
                f"expected exactly one dry-run account-unblock execution, got {len(executions)}"
            )
        elif not executions[0]["dry_run"]:
            failures.append("the account-unblock execution was not marked dry-run")
        elif executions[0]["action_endpoint"] != "/unlock-account/12000001":
            failures.append("the execution targeted the wrong account-unblock endpoint")
    elif scenario_name == "complete_vpn":
        vpn_executions = [row for row in executions if row["action_endpoint"] == "/create-account"]
        if len(vpn_executions) != 1:
            failures.append(f"expected one dry-run VPN execution, got {len(vpn_executions)}")
        elif not vpn_executions[0]["dry_run"]:
            failures.append("the VPN execution was not marked dry-run")
    elif scenario_name == "quoted_closed_history":
        if any(row["pdv_code"] == "55000002" for row in operations):
            failures.append("quoted closed-history PDV became an operation")
        if not any(
            row["action"] == "password_reset" and row["pdv_code"] == "44000001"
            for row in operations
        ):
            failures.append("current password-reset PDV was not identified")
    elif scenario_name == "multi_operation_ambiguous" and executions:
        failures.append("ambiguous multi-operation attribution produced an execution")
    elif scenario_name == "automated_out_of_office":
        if audit["model_runs"]:
            failures.append("automated message reached model inference")
        if audit["emails"][0]["status"] != "ignored":
            failures.append("automated message was not ignored")
    return failures
