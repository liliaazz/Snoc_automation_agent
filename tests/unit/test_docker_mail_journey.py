from email import policy
from email.parser import BytesParser

from snoc_agent.evaluation.journey_quality import journey_quality_failures
from snoc_agent.evaluation.mail_journeys import MAIL_JOURNEY_SCENARIOS, build_journey_message


def test_complete_unblock_requires_an_execution() -> None:
    audit = {
        "operations": [{"action": "account_unblock"}],
        "executions": [],
    }

    failures = journey_quality_failures("complete_unblock", audit, replies=[])

    assert failures == ["expected exactly one dry-run account-unblock execution, got 0"]


def test_complete_unblock_accepts_only_the_expected_dry_run_endpoint() -> None:
    audit = {
        "operations": [{"action": "account_unblock"}],
        "executions": [
            {
                "dry_run": True,
                "action_endpoint": "/unlock-account/12000001",
            }
        ],
    }

    assert journey_quality_failures("complete_unblock", audit, replies=[]) == []


def test_graph_audit_allows_completed_ingress_only_duplicate_runs() -> None:
    email_id = "email-1"
    canonical_id = "run-canonical"
    duplicate_id = "run-duplicate"
    audit = {
        "emails": [{"id": email_id}],
        "operations": [{"action": "account_unblock"}],
        "executions": [
            {
                "dry_run": True,
                "action_endpoint": "/unlock-account/12000001",
            }
        ],
        "workflow_runs": [
            {
                "id": canonical_id,
                "inbound_email_id": email_id,
                "status": "completed",
            },
            {
                "id": duplicate_id,
                "inbound_email_id": email_id,
                "status": "completed",
            },
        ],
        "workflow_events": [
            {
                "workflow_run_id": canonical_id,
                "sequence": sequence,
                "agent": agent,
                "status": "succeeded",
            }
            for sequence, agent in enumerate(
                ["ingress", "security", "nlu", "policy", "fulfilment"], start=1
            )
        ]
        + [
            {
                "workflow_run_id": duplicate_id,
                "sequence": 1,
                "agent": "ingress",
                "status": "terminal",
            }
        ],
    }

    assert journey_quality_failures("complete_unblock", audit, replies=[]) == []


def test_graph_audit_accepts_a_real_terminal_prefix_route() -> None:
    email_id = "email-1"
    run_id = "run-terminal-at-nlu"
    audit = {
        "emails": [{"id": email_id}],
        "operations": [],
        "executions": [],
        "workflow_runs": [
            {
                "id": run_id,
                "inbound_email_id": email_id,
                "status": "completed",
            }
        ],
        "workflow_events": [
            {
                "workflow_run_id": run_id,
                "sequence": sequence,
                "agent": agent,
                "status": "terminal" if agent == "nlu" else "succeeded",
            }
            for sequence, agent in enumerate(["ingress", "security", "nlu"], start=1)
        ],
    }

    assert journey_quality_failures("multi_operation_ambiguous", audit, replies=[]) == []


def test_journey_subjects_look_like_normal_requester_messages() -> None:
    assert len(MAIL_JOURNEY_SCENARIOS) == 6
    assert all("SNOC" not in scenario.subject.upper() for scenario in MAIL_JOURNEY_SCENARIOS)
    assert all("E2E" not in scenario.subject.upper() for scenario in MAIL_JOURNEY_SCENARIOS)
    assert len({scenario.subject for scenario in MAIL_JOURNEY_SCENARIOS}) == len(
        MAIL_JOURNEY_SCENARIOS
    )


def test_journey_identifiers_are_hidden_in_headers() -> None:
    raw = build_journey_message(
        sender="person@example.test",
        recipient="agent@example.test",
        subject="Je ne reçois plus les codes",
        body="Bonjour",
        message_id="<request@example.test>",
        test_run_id="run-42",
        test_case="incomplete_otp_thread",
    )

    parsed = BytesParser(policy=policy.default).parsebytes(raw)

    assert parsed["Subject"] == "Je ne reçois plus les codes"
    assert parsed["X-SNOC-Test-Run"] == "docker-e2e"
    assert parsed["X-SNOC-Test-Run-ID"] == "run-42"
    assert parsed["X-SNOC-Test-Case"] == "incomplete_otp_thread"
