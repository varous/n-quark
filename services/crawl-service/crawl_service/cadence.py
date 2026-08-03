"""Deterministic, pure cadence calculation (Phase 2).

Given the current time and an event's known timing, decide when it should next be captured and why.
No I/O, no randomness — fully unit-testable. Returns ``(next_capture_at | None, reason)``; a ``None``
next-capture means "stop tracking".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

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

_TERMINAL_STATUSES = frozenset({"STOPPED", "CANCELLED", "NEEDS_REVIEW"})
_POST_EVENT_OFFSETS_DAYS = (1, 3, 7)


@dataclass(frozen=True)
class CadenceConfig:
    far_future_hours: int = 24
    mid_hours: int = 12
    final_hours: int = 4
    onsale_burst_hours: int = 2
    event_day_hours: int = 2


def compute_cadence(
    now: datetime,
    *,
    starts_at: datetime | None,
    on_sale_at: datetime | None = None,
    tracking_status: str = "ACTIVE",
    config: CadenceConfig | None = None,
) -> tuple[datetime | None, str]:
    cfg = config or CadenceConfig()

    if tracking_status in _TERMINAL_STATUSES:
        return None, TRACKING_STOPPED

    # Post-event follow-ups take precedence once the event has started/passed.
    if starts_at is not None and now >= starts_at:
        for days in _POST_EVENT_OFFSETS_DAYS:
            follow = starts_at + timedelta(days=days)
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
