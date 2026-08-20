"""Central UTC normalization for database and API timestamps."""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return an offset-aware current UTC timestamp."""

    return datetime.now(UTC)


def ensure_utc(value: datetime | None) -> datetime | None:
    """Normalize a timestamp to aware UTC.

    SQLite commonly returns naive values even for ``DateTime(timezone=True)``.
    Those persisted values are interpreted as UTC, matching this application's
    timestamp-writing policy.
    """

    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utc_iso(value: datetime | None) -> str | None:
    """Serialize a timestamp as aware UTC ISO-8601."""

    normalized = ensure_utc(value)
    return normalized.isoformat() if normalized is not None else None


def elapsed_seconds(started_at: datetime | None, completed_at: datetime | None) -> float | None:
    """Return a safe duration for timestamps loaded from any supported database."""

    start = ensure_utc(started_at)
    completed = ensure_utc(completed_at)
    if start is None or completed is None:
        return None
    return (completed - start).total_seconds()
