from __future__ import annotations

import json
from email.message import EmailMessage

import pytest

from snoc_agent.ai.candidate_extractor import extract_numeric_candidates
from snoc_agent.ai.confidence import logprob_margin, logprob_metrics
from snoc_agent.ai.context_builder import ContextBuilder
from snoc_agent.ai.schemas import EmailAnalysis, ProposedOperation
from snoc_agent.ai.structured_output import parse_structured_output
from snoc_agent.ai.verifier import SemanticVerifier
from snoc_agent.domain.entities import OperationSnapshot
from snoc_agent.domain.enums import OperationAction
from snoc_agent.domain.errors import StructuredOutputError
from snoc_agent.mail.parser import parse_email


def _parsed_message(body: str, *, subject: str = "Demande"):
    message = EmailMessage()
    message["Message-ID"] = "<context@example.test>"
    message["From"] = "manager@example.test"
    message["To"] = "snoc@example.test"
    message["Subject"] = subject
    message.set_content(body)
    return parse_email(message.as_bytes())


def _valid_analysis_payload() -> dict[str, object]:
    return {
        "message_kind": "new_request",
        "referenced_existing_operation_ids": [],
        "operations": [
            {
                "local_operation_id": "OP-01",
                "action": "account_unblock",
                "pdv_code": "12345678",
                "phone": None,
                "additional_fields": {},
                "missing_fields": [],
                "evidence": [
                    {
                        "field_name": "pdv_code",
                        "value": "12345678",
                        "source": "latest_user_message",
                        "evidence_text": "PDV 12345678",
                        "support": "supported",
                    }
                ],
                "ambiguity_reasons": [],
                "raw_action_confidence": 0.91,
                "raw_field_confidence": {"pdv_code": 0.95},
            }
        ],
        "new_request_present": True,
        "contradiction_with_stored_state": False,
        "contradiction_details": [],
        "unresolved_ambiguities": [],
    }


def test_numeric_candidates_preserve_normalized_value_section_offsets_and_context() -> None:
    text = "PDV 12 34 56 78, téléphone +213 (777) 888-999 pour le responsable."

    candidates = extract_numeric_candidates(text, section="latest_user_message")

    assert [(candidate.value, candidate.kind_hint) for candidate in candidates] == [
        ("12345678", "pdv_or_unknown"),
        ("+213777888999", "phone_or_unknown"),
    ]
    for candidate in candidates:
        assert text[candidate.start : candidate.end] == candidate.raw_value
        assert candidate.section == "latest_user_message"
        assert candidate.raw_value in candidate.context


def test_candidate_extraction_labels_history_without_promoting_it_to_current() -> None:
    candidates = extract_numeric_candidates("Ancien PDV 11111111", section="quoted_closed_history")

    assert len(candidates) == 1
    assert candidates[0].value == "11111111"
    assert candidates[0].section == "quoted_closed_history"


def test_candidate_extraction_does_not_join_numbered_list_items_across_lines() -> None:
    candidates = extract_numeric_candidates(
        "1. Débloquer le PDV 32000001.\n2. Réinitialiser le PDV 32000002."
    )

    assert [candidate.value for candidate in candidates] == ["32000001", "32000002"]


def test_candidate_extraction_does_not_merge_darija_word_suffix_digit() -> None:
    candidates = extract_numeric_candidates(
        "Beddel OTP ta3 81000013 l numéro 0550123413.",
        section="latest_user_message",
    )

    assert [candidate.value for candidate in candidates] == ["81000013", "0550123413"]
    assert [candidate.kind_hint for candidate in candidates] == [
        "pdv_or_unknown",
        "phone_or_unknown",
    ]


def test_verifier_payload_keeps_subject_separate_from_current_body() -> None:
    payload = SemanticVerifier.input_payload(
        context_mode="new_request",
        subject="Reset PDV 81000004",
        latest_user_message="Débloquez le PDV 81000005.",
        stored_operation_state={},
        proposed_operation=ProposedOperation(
            local_operation_id="op-1",
            action="account_unblock",
            pdv_code="81000005",
            phone=None,
        ),
        candidate_evidence=[],
        correlation_strength="new",
    )

    assert payload["subject"] == "Reset PDV 81000004"
    assert payload["latest_user_message"] == "Débloquez le PDV 81000005."


def test_standard_token_logprobs_produce_an_uncalibrated_margin_diagnostic() -> None:
    payload = {
        "content": [
            {
                "token": "yes",
                "logprob": -0.1,
                "top_logprobs": [
                    {"token": "yes", "logprob": -0.1},
                    {"token": "no", "logprob": -1.1},
                ],
            },
            {
                "token": "}",
                "logprob": -0.2,
                "top_logprobs": [
                    {"token": "}", "logprob": -0.2},
                    {"token": "]", "logprob": -0.7},
                ],
            },
        ]
    }

    metrics = logprob_metrics(payload)

    assert metrics["minimum_token_margin"] == pytest.approx(0.5)
    assert metrics["mean_token_margin"] == pytest.approx(0.75)
    assert logprob_margin(payload) == pytest.approx(0.5)


def test_new_request_context_excludes_quoted_closed_history_values() -> None:
    parsed = _parsed_message(
        "Merci de débloquer le PDV 22222222.\n\n"
        "-----Message d'origine-----\n"
        "Ancienne demande terminée pour le PDV 11111111."
    )

    context = ContextBuilder().new_request(parsed)

    assert context["mode"] == "new_request"
    assert context["latest_user_message"] == "Merci de débloquer le PDV 22222222."
    assert "11111111" not in context["text_since_last_closed_request"]
    assert context["closed_history_summary"] is None
    assert [item["value"] for item in context["numeric_candidates"]] == ["22222222"]


def test_uncertain_segmentation_adds_a_bounded_labelled_thread_candidate() -> None:
    parsed = _parsed_message(
        "Merci de débloquer le PDV 22222222.\n> Ancienne demande terminée pour le PDV 11111111."
    )

    context = ContextBuilder().new_request(parsed)

    assert context["segmentation_confidence"] == 0.75
    assert context["relevant_thread_context"] == {
        "section": "relevant_thread_context",
        "trust": "untrusted_segmentation_candidate",
        "text": "> Ancienne demande terminée pour le PDV 11111111.",
    }
    assert [item["value"] for item in context["numeric_candidates"]] == ["22222222"]


def test_clarification_context_keeps_stored_state_separate_from_latest_candidates() -> None:
    parsed = _parsed_message("Le nouveau numéro est 777888999.")
    operation = OperationSnapshot(
        operation_id="operation-1",
        action=OperationAction.OTP_NUMBER_CHANGE,
        pdv_code="12345678",
        phone=None,
        missing_fields=["new_phone"],
    )

    context = ContextBuilder().clarification_reply(
        parsed,
        request_reference="SNOC-REQ-A84F91C274D2",
        previous_agent_question="Merci de préciser le nouveau numéro OTP.",
        operations=[operation],
    )

    assert context["mode"] == "clarification_reply"
    assert context["target_operations"] == [
        {
            "operation_id": "operation-1",
            "action": "otp_number_change",
            "known_fields": {"pdv_code": "12345678", "phone": None},
            "missing_fields": ["new_phone"],
        }
    ]
    assert [candidate.value for candidate in ContextBuilder.current_candidates(context)] == [
        "777888999"
    ]
    assert all(
        item["value"] != "12345678" for item in context["numeric_candidates_from_latest_reply"]
    )


def test_possible_follow_up_context_never_enables_automatic_execution() -> None:
    parsed = _parsed_message("Concernant le PDV 12345678, voici la suite.")

    context = ContextBuilder().possible_follow_up(
        parsed,
        possible_open_requests=[{"request_reference": "SNOC-REQ-A84F91C274D2"}],
    )

    assert context["correlation_strength"] == "weak"
    assert context["automatic_execution_allowed"] is False


def test_structured_output_accepts_one_valid_json_object_with_optional_fence() -> None:
    payload = _valid_analysis_payload()
    raw = f"```json\n{json.dumps(payload)}\n```"

    parsed = parse_structured_output(raw, EmailAnalysis)

    assert isinstance(parsed, EmailAnalysis)
    assert parsed.operations[0].pdv_code == "12345678"


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '{"message_kind": "new_request"} trailing prose',
        '{"message_kind": "new_request"}{"message_kind": "ambiguous"}',
        "not JSON",
    ],
)
def test_structured_output_rejects_non_object_or_non_single_json(raw: str) -> None:
    with pytest.raises(StructuredOutputError):
        parse_structured_output(raw, EmailAnalysis)


def test_structured_output_rejects_extra_keys_and_lax_type_coercion() -> None:
    extra = _valid_analysis_payload()
    extra["model_says_execute"] = True
    coerced = _valid_analysis_payload()
    coerced["new_request_present"] = "true"

    with pytest.raises(StructuredOutputError, match="violates schema"):
        parse_structured_output(json.dumps(extra), EmailAnalysis)
    with pytest.raises(StructuredOutputError, match="violates schema"):
        parse_structured_output(json.dumps(coerced), EmailAnalysis)


def test_structured_output_rejects_invalid_confidence_and_evidence_source() -> None:
    invalid_confidence = _valid_analysis_payload()
    invalid_confidence["operations"][0]["raw_action_confidence"] = 1.1  # type: ignore[index]
    invalid_source = _valid_analysis_payload()
    invalid_source["operations"][0]["evidence"][0]["source"] = "email_instruction"  # type: ignore[index]

    with pytest.raises(StructuredOutputError):
        parse_structured_output(json.dumps(invalid_confidence), EmailAnalysis)
    with pytest.raises(StructuredOutputError):
        parse_structured_output(json.dumps(invalid_source), EmailAnalysis)
