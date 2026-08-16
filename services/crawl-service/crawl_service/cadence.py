"""Deterministic, pure cadence calculation (Phase 2).

Given the current time and an event's known timing, decide when it should next be captured and why.
No I/O, no randomness — fully unit-testable. Returns ``(next_capture_at | None, reason)``; a ``None``
next-capture means "stop tracking".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from crawl_service.lifecycle import provider_lifecycle as normalize_lifecycle
from crawl_service.lifecycle import temporal_state

# Cadence reasons (stable, explainable labels).
FAR_FUTURE = "far_future_or_not_on_sale"
MID = "mid_15_30_days"
FINAL = "final_14_days"
EVENT_DAY = "event_day"
ONSALE_BURST = "onsale_first_48h"
POST_EVENT = "post_event_followup"
POST_EVENT_COMPLETE = "post_event_complete"
NO_DATE = "no_event_date"
TRACKING_STOPPED = "tracking_stopped"
CANCELLED_CONFIRMATION = "cancelled_confirmation"
POSTPONED_MONITORING = "postponed_monitoring"
ONGOING = "ongoing"

_TERMINAL_STATUSES = frozenset({"STOPPED", "NEEDS_REVIEW"})


@dataclass(frozen=True)
class CadenceConfig:
    far_future_hours: int = 24
    mid_hours: int = 12
    final_hours: int = 4
    onsale_burst_hours: int = 2
    event_day_hours: int = 2
    post_event_offsets_days: tuple[int, ...] = (1, 3, 7)
    cancelled_confirmation_hours: int = 24
    postponed_hours: int = 48


def compute_cadence(
    now: datetime,
    *,
    starts_at: datetime | None,
    ends_at: datetime | None = None,
    event_date: str | None = None,
    local_timezone: str | None = None,
    on_sale_at: datetime | None = None,
    tracking_status: str = "ACTIVE",
    provider_lifecycle: str | None = None,
    lifecycle_observed_at: datetime | None = None,
    config: CadenceConfig | None = None,
) -> tuple[datetime | None, str]:
    cfg = config or CadenceConfig()

    if tracking_status in _TERMINAL_STATUSES:
        return None, TRACKING_STOPPED

    lifecycle = normalize_lifecycle(provider_lifecycle or tracking_status)
    if lifecycle == "CANCELLED":
        due = (lifecycle_observed_at or now) + timedelta(hours=cfg.cancelled_confirmation_hours)
        return (due, CANCELLED_CONFIRMATION) if due > now else (None, TRACKING_STOPPED)
    if lifecycle == "POSTPONED":
        return now + timedelta(hours=cfg.postponed_hours), POSTPONED_MONITORING

    temporal = temporal_state(starts_at=starts_at, ends_at=ends_at, event_date=event_date,
                              evaluated_at=now, local_timezone=local_timezone)
    if temporal["temporal_state"] == "ONGOING":
        return now + timedelta(hours=cfg.event_day_hours), ONGOING
    # Post-event follow-ups take precedence after the trustworthy end (or start if duration unknown).
    if temporal["temporal_state"] == "PAST":
        anchor = ends_at or starts_at
        if anchor is None:
            return None, POST_EVENT_COMPLETE
        for days in cfg.post_event_offsets_days:
            follow = anchor + timedelta(days=days)
            if follow > now:
                return follow, POST_EVENT
        return None, POST_EVENT_COMPLETE

    if starts_at is None:
        # No event date -> conservative far-future cadence.
        return now + timedelta(hours=cfg.far_future_hours), NO_DATE

    delta_days = (starts_at - now).total_seconds() / 86400.0

    # Event day: within the final 24h before start.
    if delta_days < 1:
        return now + timedelta(hours=cfg.event_day_hours), EVENT_DAY

    # First 48h after on-sale (when the timestamp is known) — a high-movement window.
    if on_sale_at is not None and on_sale_at <= now <= on_sale_at + timedelta(hours=48):
        return now + timedelta(hours=cfg.onsale_burst_hours), ONSALE_BURST

    if delta_days <= 14:
        return now + timedelta(hours=cfg.final_hours), FINAL
    if delta_days <= 30:
        return now + timedelta(hours=cfg.mid_hours), MID
    return now + timedelta(hours=cfg.far_future_hours), FAR_FUTURE
