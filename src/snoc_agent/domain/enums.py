"""Canonical domain enumerations. Database rows store their string values."""

from __future__ import annotations

from enum import StrEnum


class OperationAction(StrEnum):
    VPN_ACCESS = "vpn_access"
    OTP_NUMBER_CHANGE = "otp_number_change"
    ACCOUNT_UNBLOCK = "account_unblock"
    PASSWORD_RESET = "password_reset"
    UNKNOWN = "unknown"


class AnalysisOutcome(StrEnum):
    IRRELEVANT = "irrelevant"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class ProcessingStatus(StrEnum):
    STORED = "stored"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"
    QUARANTINED = "quarantined"


class ConversationStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class RequestKind(StrEnum):
    NEW = "new"
    FOLLOW_UP = "follow_up"
    CORRECTION = "correction"
    MIXED = "mixed"


class RequestStatus(StrEnum):
    NEW = "NEW"
    ANALYZING = "ANALYZING"
    ACTIVE = "ACTIVE"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    READY_FOR_VALIDATION = "READY_FOR_VALIDATION"
    ESCALATED = "ESCALATED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class OperationStatus(StrEnum):
    NEW = "NEW"
    NEEDS_INFORMATION = "NEEDS_INFORMATION"
    READY_FOR_VALIDATION = "READY_FOR_VALIDATION"
    ESCALATED = "ESCALATED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ClarificationStatus(StrEnum):
    PENDING_SEND = "pending_send"
    OPEN = "open"
    RESOLVED = "resolved"
    EXPIRED = "expired"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"
    FAILED = "failed"


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class FinalDecision(StrEnum):
    AUTO_EXECUTE = "AUTO_EXECUTE"
    ASK_FOR_INFORMATION = "ASK_FOR_INFORMATION"
    ESCALATE = "ESCALATE"
    IGNORE = "IGNORE"
    MARK_DUPLICATE = "MARK_DUPLICATE"
    REVIEW_CORRECTION = "REVIEW_CORRECTION"


class CorrelationStrength(StrEnum):
    STRONG = "strong"
    WEAK = "weak"
    NEW = "new"
    CONFLICT = "conflict"
    NONE = "none"


LEGACY_ACTION_MAPPING: dict[str, OperationAction] = {
    "vpn": OperationAction.VPN_ACCESS,
    "otp": OperationAction.OTP_NUMBER_CHANGE,
    "locked": OperationAction.ACCOUNT_UNBLOCK,
    "reset": OperationAction.PASSWORD_RESET,
}


TERMINAL_OPERATION_STATUSES = {
    OperationStatus.COMPLETED,
    OperationStatus.FAILED,
    OperationStatus.CANCELLED,
    OperationStatus.ESCALATED,
}
