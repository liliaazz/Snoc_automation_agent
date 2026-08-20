"""Operation field revision and safe state mutation helpers."""

from __future__ import annotations

from sqlalchemy.orm import Session

from snoc_agent.ai.schemas import ProposedOperation
from snoc_agent.db.models import EmailMessage, FieldRevision, Operation
from snoc_agent.domain.enums import OperationStatus
from snoc_agent.domain.state_machine import assert_operation_transition
from snoc_agent.domain.value_objects import canonical_action, normalize_numeric, required_fields


def set_operation_status(operation: Operation, target: OperationStatus) -> None:
    current = OperationStatus(operation.status)
    assert_operation_transition(current, target)
    operation.status = target.value


def effective_missing_fields(operation: Operation) -> list[str]:
    values = {
        "pdv_code": operation.pdv_code,
        "phone": operation.phone,
        "new_phone": operation.phone,
    }
    return [
        field for field in required_fields(canonical_action(operation.action)) if not values[field]
    ]


def apply_proposal_fields(
    session: Session,
    *,
    operation: Operation,
    proposal: ProposedOperation,
    source_email: EmailMessage,
    reason: str,
    model_run_id: object | None = None,
) -> None:
    updates = {
        "pdv_code": normalize_numeric(proposal.pdv_code),
        "phone": normalize_numeric(proposal.phone, keep_leading_plus=True),
    }
    for field_name, new_value in updates.items():
        old_value = getattr(operation, field_name)
        if new_value is None or new_value == old_value:
            continue
        session.add(
            FieldRevision(
                operation_id=operation.id,
                field_name=field_name,
                old_value=old_value,
                new_value=new_value,
                source_email_id=source_email.id,
                model_run_id=model_run_id,
                reason=reason,
            )
        )
        setattr(operation, field_name, new_value)
        operation.current_revision += 1
    operation.missing_fields = effective_missing_fields(operation)
