"""Crawl-side implementation of the Event lifecycle domain contract.

Kept fixture-compatible with the Admin BFF implementation because services deploy independently.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo


def _dt(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else None
    if not isinstance(value, str) or len(value.strip()) <= 10:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _day(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def temporal_state(*, starts_at=None, ends_at=None, event_date=None,
                   evaluated_at=None, local_timezone=None) -> dict:
    now = evaluated_at or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    start, end = _dt(starts_at), _dt(ends_at)
    if start and end:
        state = "PAST" if end < now else ("UPCOMING" if start > now else "ONGOING")
        basis = "START_END_DATETIME"
    elif start:
        state = "UPCOMING" if start > now else "PAST"
        basis = "START_DATETIME_ONLY"
    else:
        day = _day(event_date) or (_day(starts_at) if isinstance(starts_at, str) and len(starts_at.strip()) == 10 else None)
        if day and local_timezone:
            today = now.astimezone(ZoneInfo(local_timezone)).date()
            state = "PAST" if day < today else ("UPCOMING" if day > today else "UNKNOWN")
            basis = "DATE_ONLY"
        else:
            state, basis = "UNKNOWN", "UNKNOWN"
    return {"temporal_state": state, "temporal_basis": basis,
            "effective_start_at": start, "effective_end_at": end,
            "source_time_precision": basis, "evaluated_at": now}


def provider_lifecycle(value) -> str:
    raw = str(value or "").rsplit("/", 1)[-1].upper()
    raw = raw.removeprefix("EVENT")
    return raw if raw in {"SCHEDULED", "CANCELLED", "POSTPONED", "RESCHEDULED"} else "UNKNOWN"
