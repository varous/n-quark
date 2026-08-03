"""On-sale timing semantics (Phase 2.1). Never invents an exact on_sale_at.

- first observed already on sale -> only `first_ticket_state_seen_at`;
- observed not-on-sale then on-sale -> an interval [start, end], never a point;
- an explicit source on-sale timestamp is handled separately (DIRECT_SOURCE, not here).
"""

from __future__ import annotations

from datetime import datetime

from crawl_service.enrichment.registry import TEMPORAL_INTERVAL, TEMPORAL_OBSERVATION, Candidate


def onsale_candidates(
    *,
    observed_at: datetime,
    currently_on_sale: bool,
    prev_first_ticket_state_seen_at: datetime | None,
    prev_last_not_on_sale_at: datetime | None,
) -> list[Candidate]:
    out: list[Candidate] = []

    def add(field_name, value, conf) -> None:
        out.append(Candidate(
            field_name=field_name, candidate_value=value, source_type=TEMPORAL_OBSERVATION,
            extraction_method=TEMPORAL_INTERVAL, confidence=conf, observed_at=observed_at,
        ).normalize())

    if not currently_on_sale:
        # Successful observation that tickets are not yet on sale.
        add("last_observed_not_on_sale_at", observed_at.isoformat(), 0.6)
        return out

    # currently on sale
    if prev_first_ticket_state_seen_at is None:
        add("first_ticket_state_seen_at", observed_at.isoformat(), 0.6)
    if prev_last_not_on_sale_at is not None:
        # We bracketed the transition: window is (last-not-on-sale, first-on-sale]. NOT a point.
        add("estimated_on_sale_window_start", prev_last_not_on_sale_at.isoformat(), 0.6)
        add("estimated_on_sale_window_end", observed_at.isoformat(), 0.6)
    return out
