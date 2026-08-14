"""Explainable Event lifecycle derivation for product read models.

Temporal state is a relationship between observed schedule evidence and ``evaluated_at``.
It is deliberately independent from provider lifecycle and never claims completion.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

TEMPORAL_STATES = frozenset({"UPCOMING", "ONGOING", "PAST", "UNKNOWN"})
PROVIDER_LIFECYCLES = frozenset({"SCHEDULED", "CANCELLED", "POSTPONED", "RESCHEDULED", "UNKNOWN"})
_SCHEMA_LIFECYCLE = {
    "https://schema.org/eventscheduled": "SCHEDULED",
    "eventscheduled": "SCHEDULED",
    "https://schema.org/eventcancelled": "CANCELLED",
    "eventcancelled": "CANCELLED",
    "https://schema.org/eventpostponed": "POSTPONED",
    "eventpostponed": "POSTPONED",
    "https://schema.org/eventrescheduled": "RESCHEDULED",
    "eventrescheduled": "RESCHEDULED",
}


def normalize_provider_lifecycle(value: object) -> str:
    if not value:
        return "UNKNOWN"
    text = str(value).strip().lower()
    return _SCHEMA_LIFECYCLE.get(text, text.upper() if text.upper() in PROVIDER_LIFECYCLES else "UNKNOWN")


def _datetime(value: object) -> datetime | None:
    if not value or not isinstance(value, str) or len(value.strip()) <= 10:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    # A naive clock time has no defensible instant. Do not silently interpret it as server time.
    return parsed if parsed.tzinfo is not None else None


def _date(value: object) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def derive_temporal_state(
    *, starts_at: object = None, ends_at: object = None, event_date: object = None,
    evaluated_at: datetime | None = None, local_timezone: str | None = None,
) -> dict[str, object | None]:
    """Derive state without inventing a duration or a timezone.

    A start-only datetime becomes PAST once it has passed; it is never called ONGOING because no
    end/duration evidence exists. A date-only event on local today is UNKNOWN: midnight and an
    all-day duration would both be fabricated.
    """
    now = evaluated_at or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    start, end = _datetime(starts_at), _datetime(ends_at)
    if start and end:
        state = "PAST" if end < now else ("UPCOMING" if start > now else "ONGOING")
        basis = "START_END_DATETIME"
    elif start:
        state = "UPCOMING" if start > now else "PAST"
        basis = "START_DATETIME_ONLY"
    else:
        day = _date(event_date) or (_date(starts_at) if isinstance(starts_at, str) and len(starts_at.strip()) == 10 else None)
        if day and local_timezone:
            today = now.astimezone(ZoneInfo(local_timezone)).date()
            state = "PAST" if day < today else ("UPCOMING" if day > today else "UNKNOWN")
            basis = "DATE_ONLY"
        else:
            state, basis = "UNKNOWN", "UNKNOWN"
    return {
        "temporal_state": state,
        "temporal_basis": basis,
        "effective_start_at": start.isoformat() if start else None,
        "effective_end_at": end.isoformat() if end else None,
        "source_time_precision": basis,
        "evaluated_at": now.isoformat(),
        "local_timezone": local_timezone,
    }


def lifecycle_from_properties(properties: dict, *, evaluated_at: datetime | None = None) -> dict:
    # District and Boshow are explicitly India-market contracts. This default is source semantics,
    # never the host/server timezone; other sources remain uncertain unless they carry a zone.
    source = str(properties.get("source") or "").lower()
    timezone = properties.get("source_timezone") or ("Asia/Kolkata" if source in {"district", "boshow"} else None)
    temporal = derive_temporal_state(
        starts_at=properties.get("starts_at"), ends_at=properties.get("ends_at"),
        event_date=properties.get("event_date"), evaluated_at=evaluated_at,
        local_timezone=timezone,
    )
    temporal["provider_lifecycle"] = normalize_provider_lifecycle(properties.get("provider_lifecycle"))
    return temporal
