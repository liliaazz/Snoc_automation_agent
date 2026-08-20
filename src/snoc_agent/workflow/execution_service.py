"""Durable, idempotent business-operation execution."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from snoc_agent.business_api import BusinessAPI, BusinessAPIError, BusinessAPITransportError
from snoc_agent.datetime_utils import ensure_utc, utc_now
from snoc_agent.db.models import (
    BusinessRequest,
    EmailMessage,
    Execution,
    Operation,
    ScheduledExecution,
)
from snoc_agent.db.repositories import ExecutionRepository
from snoc_agent.db.session import SessionFactory, session_scope
from snoc_agent.domain.enums import (
    ExecutionStatus,
    FinalDecision,
    OperationAction,
    OperationStatus,
)
from snoc_agent.domain.errors import UnsafeExecutionError
from snoc_agent.domain.state_machine import assert_operation_transition
from snoc_agent.domain.value_objects import canonical_action


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    execution_id: uuid.UUID
    status: ExecutionStatus
    detail: str


class ScheduledExecutionStatus(StrEnum):
    SCHEDULED = "scheduled"
    DISPATCHING = "dispatching"
    DISPATCHED = "dispatched"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SchedulingOutcome:
    scheduled_execution_id: uuid.UUID
    status: ScheduledExecutionStatus
    idempotency_key: str
    not_before: datetime
    detail: str


@dataclass(frozen=True, slots=True)
class ScheduledDispatchOutcome:
    scheduled_execution_id: uuid.UUID
    status: ScheduledExecutionStatus
    detail: str
    execution: ExecutionOutcome | None = None


class ExecutionService:
    def __init__(
        self,
        session_factory: SessionFactory,
        business_api: BusinessAPI,
        *,
        vpn_allowed_additional_fields: frozenset[str] = frozenset(),
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.session_factory = session_factory
        self.business_api = business_api
        self.vpn_allowed_additional_fields = vpn_allowed_additional_fields
        self.clock = clock

    @staticmethod
    def idempotency_key(operation: Operation) -> str:
        return f"{operation.id}:{operation.current_revision}"

    def schedule(
        self,
        operation_id: uuid.UUID,
        *,
        source_email_id: uuid.UUID,
        not_before: datetime,
    ) -> SchedulingOutcome:
        """Durably queue one immutable operation revision for later dispatch.

        Repeating the call for the same operation revision returns the original
        row.  A prior real execution is never converted back into a queue item.
        """

        normalized_not_before = self._aware_utc(not_before, field_name="not_before")
        try:
            with session_scope(self.session_factory) as session:
                operation = session.get(Operation, operation_id)
                if operation is None:
                    raise LookupError(f"operation {operation_id} was not found")
                key = self.idempotency_key(operation)
                existing = session.scalar(
                    select(ScheduledExecution).where(ScheduledExecution.idempotency_key == key)
                )
                if existing is not None:
                    return self._scheduling_outcome(
                        existing,
                        detail="identical operation revision was already scheduled",
                    )
                if ExecutionRepository(session).by_idempotency_key(key) is not None:
                    raise UnsafeExecutionError(
                        "identical operation revision already has an execution record"
                    )
                self._assert_schedulable(operation)
                source_email = session.get(EmailMessage, source_email_id)
                if source_email is None:
                    raise LookupError(f"source email {source_email_id} was not found")
                request = session.get(BusinessRequest, operation.request_id)
                if request is None:
                    raise LookupError(f"request {operation.request_id} was not found")
                if source_email.conversation_id != request.conversation_id:
                    raise UnsafeExecutionError(
                        "source email does not belong to the operation conversation"
                    )
                scheduled = ScheduledExecution(
                    operation_id=operation.id,
                    request_id=operation.request_id,
                    operation_revision=operation.current_revision,
                    idempotency_key=key,
                    source_email_id=source_email.id,
                    not_before=normalized_not_before,
                    status=ScheduledExecutionStatus.SCHEDULED.value,
                    cancellation_data={},
                )
                session.add(scheduled)
                session.flush()
                return self._scheduling_outcome(
                    scheduled,
                    detail="operation revision scheduled for grace-period dispatch",
                )
        except IntegrityError:
            # A concurrent scheduler may have won the unique-key race.  Fetch
            # and return only that exact immutable revision; otherwise surface
            # the database failure rather than guessing.
            with session_scope(self.session_factory) as session:
                operation = session.get(Operation, operation_id)
                if operation is None:
                    raise
                key = self.idempotency_key(operation)
                existing = session.scalar(
                    select(ScheduledExecution).where(ScheduledExecution.idempotency_key == key)
                )
                if existing is None:
                    raise
                return self._scheduling_outcome(
                    existing,
                    detail="identical operation revision was concurrently scheduled",
                )

    def cancel_scheduled_for_request(
        self,
        request_id: uuid.UUID,
        *,
        reason: str,
        source_email_id: uuid.UUID | None = None,
    ) -> int:
        """Cancel every still-waiting queue item for a corrected request."""

        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("cancellation reason must not be empty")
        cancelled_at = self._now()
        with session_scope(self.session_factory) as session:
            request = session.get(BusinessRequest, request_id)
            if request is None:
                raise LookupError(f"request {request_id} was not found")
            if source_email_id is not None:
                source_email = session.get(EmailMessage, source_email_id)
                if source_email is None:
                    raise LookupError(f"source email {source_email_id} was not found")
                if source_email.conversation_id != request.conversation_id:
                    raise UnsafeExecutionError(
                        "cancellation source email does not belong to the request conversation"
                    )
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(ScheduledExecution)
                    .where(
                        ScheduledExecution.request_id == request_id,
                        ScheduledExecution.status == ScheduledExecutionStatus.SCHEDULED.value,
                    )
                    .values(
                        status=ScheduledExecutionStatus.CANCELLED.value,
                        cancellation_reason=normalized_reason[:2000],
                        cancellation_source_email_id=source_email_id,
                        cancellation_data={
                            "reason": normalized_reason[:2000],
                            "source_email_id": (
                                str(source_email_id) if source_email_id is not None else None
                            ),
                        },
                        cancelled_at=cancelled_at,
                        updated_at=cancelled_at,
                    )
                ),
            )
            return int(result.rowcount or 0)

    def dispatch_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[ScheduledDispatchOutcome]:
        """Atomically claim and dispatch due grace-period queue items."""

        if limit < 1:
            raise ValueError("dispatch limit must be at least 1")
        dispatch_at = self._aware_utc(now, field_name="now") if now is not None else self._now()
        with session_scope(self.session_factory) as session:
            candidate_ids = list(
                session.scalars(
                    select(ScheduledExecution.id)
                    .where(
                        ScheduledExecution.status == ScheduledExecutionStatus.SCHEDULED.value,
                        ScheduledExecution.not_before <= dispatch_at,
                    )
                    .order_by(ScheduledExecution.not_before, ScheduledExecution.created_at)
                    .limit(limit)
                )
            )

        outcomes: list[ScheduledDispatchOutcome] = []
        for scheduled_id in candidate_ids:
            if not self._claim(scheduled_id, dispatch_at):
                continue
            outcomes.append(self._dispatch_claimed(scheduled_id, dispatch_at))
        return outcomes

    def execute(
        self,
        operation_id: uuid.UUID,
        *,
        expected_revision: int | None = None,
        expected_idempotency_key: str | None = None,
    ) -> ExecutionOutcome:
        with session_scope(self.session_factory) as session:
            operation = session.get(Operation, operation_id)
            if operation is None:
                raise LookupError(f"operation {operation_id} was not found")
            if expected_revision is not None and operation.current_revision != expected_revision:
                raise UnsafeExecutionError(
                    f"operation {operation.id} revision changed from "
                    f"{expected_revision} to {operation.current_revision}"
                )
            key = self.idempotency_key(operation)
            if expected_idempotency_key is not None and key != expected_idempotency_key:
                raise UnsafeExecutionError(
                    "operation idempotency key changed after it was scheduled"
                )
            prior = ExecutionRepository(session).by_idempotency_key(key)
            if prior is not None:
                return ExecutionOutcome(
                    prior.id,
                    ExecutionStatus(prior.status),
                    "identical operation revision was already recorded",
                )
            if OperationStatus(operation.status) != OperationStatus.READY_FOR_VALIDATION:
                raise UnsafeExecutionError(
                    f"operation {operation.id} is {operation.status}, not READY_FOR_VALIDATION"
                )
            action = canonical_action(operation.action)
            pdv_code = operation.pdv_code
            phone = operation.phone
            additional = dict(operation.additional_payload)
            preflight_error: str | None = None
            if pdv_code is None:
                preflight_error = "PDV is absent after validation"
            elif action in {OperationAction.VPN_ACCESS, OperationAction.OTP_NUMBER_CHANGE} and (
                phone is None
            ):
                preflight_error = "phone is absent after validation"
            elif action == OperationAction.UNKNOWN:
                preflight_error = "operation action is unsupported"
            else:
                allowed = (
                    self.vpn_allowed_additional_fields
                    if action == OperationAction.VPN_ACCESS
                    else frozenset()
                )
                unexpected = set(additional) - allowed
                if unexpected:
                    preflight_error = (
                        "operation contains unapproved additional fields: "
                        + ", ".join(sorted(unexpected))
                    )
            if preflight_error:
                execution = Execution(
                    operation_id=operation.id,
                    operation_revision=operation.current_revision,
                    idempotency_key=key,
                    endpoint=f"rejected_preflight:{operation.action}",
                    request_payload={
                        "pdv_code": pdv_code,
                        "phone": phone,
                        **additional,
                    },
                    response_body={"error": preflight_error},
                    dry_run=True,
                    attempt_count=0,
                    status=ExecutionStatus.FAILED.value,
                )
                ExecutionRepository(session).add(execution)
                operation.status = OperationStatus.ESCALATED.value
                operation.execution_eligible = False
                return ExecutionOutcome(execution.id, ExecutionStatus.FAILED, preflight_error)
            assert_operation_transition(
                OperationStatus(operation.status), OperationStatus.EXECUTING
            )
            operation.status = OperationStatus.EXECUTING.value
            payload = {
                "pdv_code": operation.pdv_code,
                "phone": operation.phone,
                **operation.additional_payload,
            }
            execution = Execution(
                operation_id=operation.id,
                operation_revision=operation.current_revision,
                idempotency_key=key,
                endpoint=f"pending:{operation.action}",
                request_payload=payload,
                dry_run=True,
                attempt_count=0,
                status=ExecutionStatus.PENDING.value,
            )
            ExecutionRepository(session).add(execution)
            execution_id = execution.id

        if pdv_code is None:
            raise AssertionError("execution preflight allowed a missing PDV")
        try:
            if action == OperationAction.VPN_ACCESS:
                if phone is None:
                    raise AssertionError("execution preflight allowed a missing VPN phone")
                result = self.business_api.create_vpn_access(
                    pdv_code=pdv_code,
                    phone=phone,
                    idempotency_key=key,
                    additional_payload=additional,
                )
            elif action == OperationAction.OTP_NUMBER_CHANGE:
                if phone is None:
                    raise AssertionError("execution preflight allowed a missing OTP phone")
                result = self.business_api.update_otp(
                    pdv_code=pdv_code, new_phone=phone, idempotency_key=key
                )
            elif action == OperationAction.ACCOUNT_UNBLOCK:
                result = self.business_api.unlock_account(pdv_code=pdv_code, idempotency_key=key)
            elif action == OperationAction.PASSWORD_RESET:
                result = self.business_api.reset_password(pdv_code=pdv_code, idempotency_key=key)
            else:
                raise UnsafeExecutionError(f"unsupported action {action}")
        except Exception as exc:
            status = (
                ExecutionStatus.UNKNOWN
                if isinstance(exc, BusinessAPITransportError)
                or not isinstance(exc, BusinessAPIError)
                else ExecutionStatus.FAILED
            )
            with session_scope(self.session_factory) as session:
                stored_execution = session.get(Execution, execution_id)
                operation = session.get(Operation, operation_id)
                if stored_execution:
                    stored_execution.status = status.value
                    stored_execution.attempt_count = max(1, getattr(exc, "attempts", 1))
                    stored_execution.response_body = {
                        "error": str(exc)[:2000],
                        "exception_type": type(exc).__name__,
                    }
                if operation:
                    operation.status = OperationStatus.ESCALATED.value
                    operation.execution_eligible = False
            return ExecutionOutcome(execution_id, status, str(exc))

        with session_scope(self.session_factory) as session:
            stored_execution = session.get(Execution, execution_id)
            operation = session.get(Operation, operation_id)
            if stored_execution is None or operation is None:
                raise RuntimeError(
                    "execution or operation disappeared while API call was in flight"
                )
            stored_execution.endpoint = result.endpoint
            stored_execution.response_status = result.status_code
            stored_execution.response_body = result.response_body
            stored_execution.dry_run = result.dry_run
            stored_execution.attempt_count = result.attempts
            stored_execution.status = (
                ExecutionStatus.SUCCEEDED.value if result.success else ExecutionStatus.FAILED.value
            )
            if result.success:
                assert_operation_transition(
                    OperationStatus(operation.status), OperationStatus.COMPLETED
                )
                operation.status = OperationStatus.COMPLETED.value
                operation.execution_eligible = False
            else:
                operation.status = OperationStatus.ESCALATED.value
                operation.execution_eligible = False
            return ExecutionOutcome(
                stored_execution.id,
                ExecutionStatus(stored_execution.status),
                "business API accepted operation"
                if result.success
                else "business API rejected operation",
            )

    def _claim(self, scheduled_id: uuid.UUID, claimed_at: datetime) -> bool:
        with session_scope(self.session_factory) as session:
            result = cast(
                CursorResult[Any],
                session.execute(
                    update(ScheduledExecution)
                    .where(
                        ScheduledExecution.id == scheduled_id,
                        ScheduledExecution.status == ScheduledExecutionStatus.SCHEDULED.value,
                        ScheduledExecution.not_before <= claimed_at,
                    )
                    .values(
                        status=ScheduledExecutionStatus.DISPATCHING.value,
                        claimed_at=claimed_at,
                        updated_at=claimed_at,
                    )
                ),
            )
            return int(result.rowcount or 0) == 1

    def _dispatch_claimed(
        self,
        scheduled_id: uuid.UUID,
        dispatch_at: datetime,
    ) -> ScheduledDispatchOutcome:
        with session_scope(self.session_factory) as session:
            scheduled = session.get(ScheduledExecution, scheduled_id)
            if scheduled is None or scheduled.status != ScheduledExecutionStatus.DISPATCHING.value:
                return ScheduledDispatchOutcome(
                    scheduled_id,
                    ScheduledExecutionStatus.FAILED,
                    "claimed schedule row disappeared or changed state",
                )
            prior = ExecutionRepository(session).by_idempotency_key(scheduled.idempotency_key)
            if prior is not None:
                execution = ExecutionOutcome(
                    prior.id,
                    ExecutionStatus(prior.status),
                    "identical operation revision was already recorded",
                )
                scheduled.status = ScheduledExecutionStatus.DISPATCHED.value
                scheduled.execution_id = prior.id
                scheduled.dispatched_at = dispatch_at
                scheduled.updated_at = dispatch_at
                return ScheduledDispatchOutcome(
                    scheduled.id,
                    ScheduledExecutionStatus.DISPATCHED,
                    "schedule reconciled with its existing execution record",
                    execution,
                )
            operation = session.get(Operation, scheduled.operation_id)
            cancellation_reason, cancellation_data = self._dispatch_blocker(
                scheduled,
                operation,
            )
            if cancellation_reason is not None:
                self._cancel_claimed(
                    scheduled,
                    reason=cancellation_reason,
                    data=cancellation_data,
                    cancelled_at=dispatch_at,
                )
                return ScheduledDispatchOutcome(
                    scheduled.id,
                    ScheduledExecutionStatus.CANCELLED,
                    cancellation_reason,
                )
            operation_id = scheduled.operation_id
            expected_revision = scheduled.operation_revision
            expected_key = scheduled.idempotency_key

        try:
            execution = self.execute(
                operation_id,
                expected_revision=expected_revision,
                expected_idempotency_key=expected_key,
            )
        except (LookupError, UnsafeExecutionError) as exc:
            detail = str(exc)[:2000]
            self._finish_cancelled(
                scheduled_id,
                reason="dispatch_precondition_changed",
                data={"error": detail},
                cancelled_at=dispatch_at,
            )
            return ScheduledDispatchOutcome(
                scheduled_id,
                ScheduledExecutionStatus.CANCELLED,
                detail,
            )
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"[:2000]
            self._finish_failed(scheduled_id, error=detail, failed_at=dispatch_at)
            return ScheduledDispatchOutcome(
                scheduled_id,
                ScheduledExecutionStatus.FAILED,
                detail,
            )

        with session_scope(self.session_factory) as session:
            scheduled = session.get(ScheduledExecution, scheduled_id)
            if scheduled is None:
                return ScheduledDispatchOutcome(
                    scheduled_id,
                    ScheduledExecutionStatus.FAILED,
                    "schedule row disappeared after execution",
                    execution,
                )
            if scheduled.status != ScheduledExecutionStatus.DISPATCHING.value:
                return ScheduledDispatchOutcome(
                    scheduled_id,
                    ScheduledExecutionStatus(scheduled.status),
                    "schedule state changed after execution",
                    execution,
                )
            scheduled.status = ScheduledExecutionStatus.DISPATCHED.value
            scheduled.execution_id = execution.execution_id
            scheduled.dispatched_at = dispatch_at
            scheduled.updated_at = dispatch_at
        return ScheduledDispatchOutcome(
            scheduled_id,
            ScheduledExecutionStatus.DISPATCHED,
            "due operation revision was dispatched",
            execution,
        )

    def _dispatch_blocker(
        self,
        scheduled: ScheduledExecution,
        operation: Operation | None,
    ) -> tuple[str | None, dict[str, object]]:
        if operation is None:
            return "operation_missing", {"operation_id": str(scheduled.operation_id)}
        if operation.request_id != scheduled.request_id:
            return "operation_request_changed", {
                "scheduled_request_id": str(scheduled.request_id),
                "observed_request_id": str(operation.request_id),
            }
        if operation.current_revision != scheduled.operation_revision:
            return "stale_operation_revision", {
                "scheduled_revision": scheduled.operation_revision,
                "observed_revision": operation.current_revision,
            }
        observed_key = self.idempotency_key(operation)
        if observed_key != scheduled.idempotency_key:
            return "idempotency_key_mismatch", {
                "scheduled_key": scheduled.idempotency_key,
                "observed_key": observed_key,
            }
        if operation.status != OperationStatus.READY_FOR_VALIDATION.value:
            return "operation_not_ready", {"observed_status": operation.status}
        if not operation.execution_eligible:
            return "operation_not_execution_eligible", {}
        if operation.final_decision != FinalDecision.AUTO_EXECUTE.value:
            return "operation_decision_not_auto_execute", {
                "observed_decision": operation.final_decision,
            }
        return None, {}

    @staticmethod
    def _cancel_claimed(
        scheduled: ScheduledExecution,
        *,
        reason: str,
        data: dict[str, object],
        cancelled_at: datetime,
    ) -> None:
        scheduled.status = ScheduledExecutionStatus.CANCELLED.value
        scheduled.cancellation_reason = reason
        scheduled.cancellation_data = {"reason": reason, **data}
        scheduled.cancelled_at = cancelled_at
        scheduled.updated_at = cancelled_at

    def _finish_cancelled(
        self,
        scheduled_id: uuid.UUID,
        *,
        reason: str,
        data: dict[str, object],
        cancelled_at: datetime,
    ) -> None:
        with session_scope(self.session_factory) as session:
            scheduled = session.get(ScheduledExecution, scheduled_id)
            if (
                scheduled is not None
                and scheduled.status == ScheduledExecutionStatus.DISPATCHING.value
            ):
                self._cancel_claimed(
                    scheduled,
                    reason=reason,
                    data=data,
                    cancelled_at=cancelled_at,
                )

    def _finish_failed(
        self,
        scheduled_id: uuid.UUID,
        *,
        error: str,
        failed_at: datetime,
    ) -> None:
        with session_scope(self.session_factory) as session:
            scheduled = session.get(ScheduledExecution, scheduled_id)
            if (
                scheduled is not None
                and scheduled.status == ScheduledExecutionStatus.DISPATCHING.value
            ):
                scheduled.status = ScheduledExecutionStatus.FAILED.value
                scheduled.last_error = error
                scheduled.updated_at = failed_at

    @staticmethod
    def _assert_schedulable(operation: Operation) -> None:
        if operation.status != OperationStatus.READY_FOR_VALIDATION.value:
            raise UnsafeExecutionError(
                f"operation {operation.id} is {operation.status}, not READY_FOR_VALIDATION"
            )
        if not operation.execution_eligible:
            raise UnsafeExecutionError(f"operation {operation.id} is not execution eligible")
        if operation.final_decision != FinalDecision.AUTO_EXECUTE.value:
            raise UnsafeExecutionError(
                f"operation {operation.id} does not have an AUTO_EXECUTE decision"
            )

    @staticmethod
    def _scheduling_outcome(
        scheduled: ScheduledExecution,
        *,
        detail: str,
    ) -> SchedulingOutcome:
        not_before = ensure_utc(scheduled.not_before)
        if not_before is None:
            raise RuntimeError("scheduled execution has no not_before timestamp")
        return SchedulingOutcome(
            scheduled_execution_id=scheduled.id,
            status=ScheduledExecutionStatus(scheduled.status),
            idempotency_key=scheduled.idempotency_key,
            not_before=not_before,
            detail=detail,
        )

    def _now(self) -> datetime:
        return self._aware_utc(self.clock(), field_name="clock")

    @staticmethod
    def _aware_utc(value: datetime, *, field_name: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        normalized = ensure_utc(value)
        if normalized is None:
            raise ValueError(f"{field_name} must be a timestamp")
        return normalized
