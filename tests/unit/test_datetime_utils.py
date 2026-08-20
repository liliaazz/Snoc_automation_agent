from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from snoc_agent.datetime_utils import elapsed_seconds, ensure_utc, utc_iso, utc_now


def test_naive_sqlite_timestamp_is_interpreted_as_utc() -> None:
    value = ensure_utc(datetime(2026, 7, 26, 10, 0))

    assert value == datetime(2026, 7, 26, 10, 0, tzinfo=UTC)


def test_offset_timestamp_is_converted_to_utc() -> None:
    source = datetime(2026, 7, 26, 11, 0, tzinfo=timezone(timedelta(hours=1)))

    assert ensure_utc(source) == datetime(2026, 7, 26, 10, 0, tzinfo=UTC)
    assert utc_iso(source) == "2026-07-26T10:00:00+00:00"


def test_elapsed_seconds_accepts_mixed_naive_and_aware_values() -> None:
    assert (
        elapsed_seconds(
            datetime(2026, 7, 26, 10, 0),
            datetime(2026, 7, 26, 10, 0, 2, tzinfo=UTC),
        )
        == 2.0
    )


def test_utc_now_is_aware() -> None:
    assert utc_now().tzinfo is UTC
