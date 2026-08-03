"""Deterministic, pure priority scoring (Phase 2). No ML — explainable components only."""

from __future__ import annotations

from datetime import datetime, timedelta


def compute_priority(
    now: datetime,
    *,
    starts_at: datetime | None,
    on_sale_at: datetime | None = None,
    last_state_change_at: datetime | None = None,
    consecutive_failures: int = 0,
    city: str | None = None,
    city_allowlist: frozenset[str] | None = None,
) -> tuple[int, str, dict[str, int]]:
    """Return (score 0-100, dominant-reason, components). Higher = capture sooner."""
    components: dict[str, int] = {}

    # Urgency by proximity to the event.
    if starts_at is None:
        components["urgency"] = 15
    else:
        delta_days = (starts_at - now).total_seconds() / 86400.0
        if delta_days < 0:
            components["urgency"] = 20   # post-event: outcome evidence still valuable
        elif delta_days < 1:
            components["urgency"] = 50   # event day
        elif delta_days <= 14:
            components["urgency"] = 40
        elif delta_days <= 30:
            components["urgency"] = 25
        else:
            components["urgency"] = 10

    # First 48h after on-sale is a high-movement window.
    if on_sale_at is not None and on_sale_at <= now <= on_sale_at + timedelta(hours=48):
        components["onsale_burst"] = 20

    # Recent commercial movement -> worth watching closely.
    if last_state_change_at is not None and (now - last_state_change_at) <= timedelta(days=7):
        components["recent_transition"] = 15

    # Strategic city.
    if city and city_allowlist and city.lower() in city_allowlist:
        components["priority_city"] = 10

    # Repeatedly failing events are de-prioritized (don't starve healthy ones).
    if consecutive_failures:
        components["failure_penalty"] = -5 * min(consecutive_failures, 3)

    score = max(0, min(100, sum(components.values())))
    dominant = max(
        (c for c in components if components[c] > 0),
        key=lambda c: components[c],
        default="urgency",
    )
    return score, dominant, components
