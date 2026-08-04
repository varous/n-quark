"""Deterministic cross-platform event matcher + blocking (Phase 3). Pure, no LLM.

Blocking bounds candidate generation; the matcher scores a pair on independent dimensions and refuses
to auto-match when a strong contradiction exists (a high title similarity never overrides a different
city/date/performer set).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from crawl_service.reconciliation.views import EventView

MATCHER_VERSION = "matcher-1"

# outcomes
AUTO_MATCH = "MATCHED"
POSSIBLE_MATCH = "POSSIBLE_MATCH"
CONFLICT = "CONFLICT"
NOT_MATCHED = "NOT_MATCHED"

# reason codes
AUTO_MATCH_THRESHOLD_MET = "AUTO_MATCH_THRESHOLD_MET"
POSSIBLE_MATCH_ONLY = "POSSIBLE_MATCH_ONLY"
STRONG_CONTRADICTION = "STRONG_CONTRADICTION"
INSUFFICIENT_SIGNALS = "INSUFFICIENT_SIGNALS"

_MEANINGFUL = 0.8
_STOP = frozenset({"the", "a", "an", "live", "show", "concert", "event", "tour", "at", "in", "with", "&", "and"})


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def _tokens(s: str | None) -> set[str]:
    return {w for w in _norm(s).split() if w and w not in _STOP and len(w) > 1}


def _sim(a: str | None, b: str | None) -> float:
    return round(SequenceMatcher(None, _norm(a), _norm(b)).ratio(), 3)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 3)


def _performer_set(v: EventView) -> set[str]:
    out: set[str] = set()
    for p in v.performers:
        out |= _tokens(p)
    return out


def in_block(left: EventView, right: EventView, *, date_tolerance_hours: int) -> tuple[bool, str]:
    """Cheap deterministic pre-filter so we never compare all pairs."""
    # date within tolerance (or unknown on one side)
    if (left.starts_at and right.starts_at
            and abs((left.starts_at - right.starts_at).total_seconds()) > date_tolerance_hours * 3600):
        return False, "DATE_OUTSIDE_TOLERANCE"
    # city compatible (same or one unknown)
    if left.city and right.city and _norm(left.city) != _norm(right.city):
        return False, "CITY_MISMATCH"
    # some shared signal: title tokens, performer overlap, or venue similarity
    if (_tokens(left.title) & _tokens(right.title)) or (_performer_set(left) & _performer_set(right)) \
            or (left.venue_name and right.venue_name and _sim(left.venue_name, right.venue_name) >= 0.6):
        return True, "BLOCKED"
    return False, "INSUFFICIENT_SIGNALS"


@dataclass
class MatchResult:
    status: str
    score: float
    components: dict[str, float] = field(default_factory=dict)
    supporting: list[str] = field(default_factory=list)
    contradicting: list[str] = field(default_factory=list)
    reason_code: str = ""


_WEIGHTS = {"title": 0.30, "performer": 0.25, "venue": 0.20, "date": 0.15, "city": 0.10, "organizer": 0.10}


def score_match(left: EventView, right: EventView, *, date_tolerance_hours: int,
                auto_threshold: float, possible_threshold: float) -> MatchResult:
    comp: dict[str, float] = {}
    supporting: list[str] = []
    contradicting: list[str] = []

    comp["title"] = _sim(left.title, right.title)
    if comp["title"] >= _MEANINGFUL:
        supporting.append("TITLE_MATCH")

    lp, rp = _performer_set(left), _performer_set(right)
    if lp and rp:
        comp["performer"] = _jaccard(lp, rp)
        if comp["performer"] >= 0.5:
            supporting.append("PERFORMER_OVERLAP")
        elif comp["performer"] == 0.0:
            contradicting.append("PERFORMER_NON_OVERLAP")  # artist-led non-overlap

    if left.venue_name and right.venue_name:
        comp["venue"] = _sim(left.venue_name, right.venue_name)
        if comp["venue"] >= _MEANINGFUL:
            supporting.append("VENUE_MATCH")

    if left.city and right.city:
        comp["city"] = 1.0 if _norm(left.city) == _norm(right.city) else 0.0
        if comp["city"] == 0.0:
            contradicting.append("CITY_MISMATCH")

    date_compatible = False
    if left.starts_at and right.starts_at:
        delta_h = abs((left.starts_at - right.starts_at).total_seconds()) / 3600.0
        if delta_h > date_tolerance_hours:
            comp["date"] = 0.0
            contradicting.append("DATE_OUTSIDE_TOLERANCE")
        else:
            comp["date"] = round(max(0.0, 1.0 - delta_h / max(date_tolerance_hours, 1)), 3)
            date_compatible = True
            if delta_h <= 24:
                supporting.append("DATE_MATCH")

    if left.organizer and right.organizer:
        comp["organizer"] = 1.0 if _norm(left.organizer) == _norm(right.organizer) else 0.0
        if comp["organizer"] == 1.0:
            supporting.append("ORGANIZER_MATCH")

    # weighted score over present components
    present = {k: v for k, v in comp.items() if k in _WEIGHTS}
    wsum = sum(_WEIGHTS[k] for k in present) or 1.0
    score = round(sum(_WEIGHTS[k] * v for k, v in present.items()) / wsum, 3)

    # venue AND organizer both differ is a soft contradiction
    if comp.get("venue", 1.0) < 0.4 and comp.get("organizer", 1.0) == 0.0:
        contradicting.append("VENUE_AND_ORGANIZER_DIFFER")

    strong = any(c in ("CITY_MISMATCH", "DATE_OUTSIDE_TOLERANCE", "PERFORMER_NON_OVERLAP")
                 for c in contradicting)
    meaningful_dims = sum(1 for k in ("title", "performer", "venue", "city", "date", "organizer")
                          if comp.get(k, 0.0) >= _MEANINGFUL)

    if strong:
        status = CONFLICT if score >= possible_threshold else NOT_MATCHED
        reason = STRONG_CONTRADICTION
    elif score >= auto_threshold and meaningful_dims >= 2 and date_compatible:
        status, reason = AUTO_MATCH, AUTO_MATCH_THRESHOLD_MET
    elif score >= possible_threshold:
        status, reason = POSSIBLE_MATCH, POSSIBLE_MATCH_ONLY
    else:
        status, reason = NOT_MATCHED, INSUFFICIENT_SIGNALS

    return MatchResult(status=status, score=score, components=comp, supporting=supporting,
                       contradicting=contradicting, reason_code=reason)
