"""Project an event-bearing social interpretation into the EXISTING reconciliation machinery (Phase 5C.2).

There is deliberately NO social event registry, NO social-only matcher, and NO social-only canonical
table. We construct the smallest ``EventView`` the existing matcher already understands from the
interpreted claims, then reuse the existing pure ``matcher.in_block`` / ``matcher.score_match`` against
the existing tracked events. The outcome is recorded as an ``event_match_candidate`` (the existing
review surface) — a hypothesis, never a canonical Event.

Safety (matches §7): the existing thresholds are passed through UNCHANGED — sparse social evidence
cannot auto-match, because the matcher already requires ``meaningful_dims >= 2`` + a compatible date and
blocks on strong contradictions. Social evidence therefore resolves to one of:
  * MATCHED_EXISTING     — the existing matcher auto-matched it to a known event (governed candidate)
  * POSSIBLE_MATCH       — plausible but below auto threshold → needs review
  * NEEDS_REVIEW         — a scored contradiction (CONFLICT) → needs review
  * NEW_EVENT_HYPOTHESIS — no existing event blocks/matches → recorded as a hypothesis, nothing created
  * INSUFFICIENT_SIGNAL  — nothing to compare against and no identity to stand alone
It never silently manufactures a canonical Event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from crawl_service.reconciliation import matcher as M
from crawl_service.reconciliation.views import EventView

SOCIAL_SOURCE = "social"

# event_candidate_status values recorded on the interpretation
MATCHED_EXISTING = "MATCHED_EXISTING"
POSSIBLE_MATCH = "POSSIBLE_MATCH"
NEEDS_REVIEW = "NEEDS_REVIEW"
NEW_EVENT_HYPOTHESIS = "NEW_EVENT_HYPOTHESIS"
INSUFFICIENT_SIGNAL = "INSUFFICIENT_SIGNAL"
NONE = "NONE"


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)).replace(tzinfo=None)  # wall-clock, matches from_graph
    except (ValueError, TypeError):
        return None


def social_event_view(*, social_mention_id: str, interpreted_fields: dict[str, Any]) -> EventView:
    """Build the smallest existing-shape ``EventView`` from interpreted claims. Only evidenced fields are
    carried; anything unknown stays ``None`` — we never invent duration/venue/artist/organizer/lifecycle.
    ``source_record_id`` is the EXACT immutable ``SocialMention`` version id, preserving the provenance
    chain candidate → interpretation → evidence version."""
    f = interpreted_fields or {}
    performers = [str(a) for a in f.get("artists", []) if a]
    return EventView(
        source=SOCIAL_SOURCE,
        source_record_id=social_mention_id,
        canonical_event_id=None,                     # social origin has no canonical event of its own
        title=str(f.get("event_name") or ""),
        starts_at=_dt(f.get("event_date")),
        city=f.get("city"),
        venue_name=f.get("venue_name"),
        organizer=f.get("organizer"),
        performers=performers,
    )


@dataclass
class ProjectionResult:
    status: str
    match: M.MatchResult | None = None
    matched_view: EventView | None = None


def project(social_view: EventView, existing: list[EventView], *, date_tolerance_hours: int,
            auto_threshold: float, possible_threshold: float) -> ProjectionResult:
    """Pure projection: reuse the existing blocker + matcher against existing events, keep the strongest
    outcome, and map it to an ``event_candidate_status``. No DB, no thresholds weakened."""
    best: M.MatchResult | None = None
    best_view: EventView | None = None
    rank = {M.AUTO_MATCH: 3, M.CONFLICT: 2, M.POSSIBLE_MATCH: 1, M.NOT_MATCHED: 0}
    for ev in existing:
        blocked, _reason = M.in_block(social_view, ev, date_tolerance_hours=date_tolerance_hours)
        if not blocked:
            continue
        res = M.score_match(social_view, ev, date_tolerance_hours=date_tolerance_hours,
                            auto_threshold=auto_threshold, possible_threshold=possible_threshold)
        if best is None or (rank[res.status], res.score) > (rank[best.status], best.score):
            best, best_view = res, ev

    if best is None:
        # Nothing blocked → no existing event to attach to. Event-bearing evidence with resolvable
        # identity stands as a NEW hypothesis (recorded only); otherwise there is not enough to say.
        has_identity = bool(social_view.title) or (
            social_view.starts_at is not None
            and (social_view.venue_name or social_view.performers or social_view.city))
        return ProjectionResult(NEW_EVENT_HYPOTHESIS if has_identity else INSUFFICIENT_SIGNAL)

    status_map = {M.AUTO_MATCH: MATCHED_EXISTING, M.POSSIBLE_MATCH: POSSIBLE_MATCH,
                  M.CONFLICT: NEEDS_REVIEW, M.NOT_MATCHED: NEW_EVENT_HYPOTHESIS}
    return ProjectionResult(status_map[best.status], match=best, matched_view=best_view)
