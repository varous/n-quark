"""Deterministic multi-label social classifier (Phase 5C.2). Pure, no LLM, no I/O.

Turns the bounded factual features extracted from a social post (``SocialMention.extracted_claims`` —
produced BEFORE the caption was dropped, see signal-service ``adapters/social.py``) into a versioned
interpretation: a set of claim types, a primary claim type, normalized interpreted fields, and a
deterministic event-bearing decision — each with an explicit reason for why it fired. It reads the
enriched ``signals`` map when present and falls back to the legacy flat ``surface_signals`` list, so it
is robust to evidence produced by either extractor version.

Epistemic guardrails encoded here:
  * SELL_OUT_CLAIM is only ever a *source claim* — never verified sell-through.
  * Ambiguous / generic-promotion / teaser evidence yields UNKNOWN or a non-event-bearing result rather
    than a forced label — "prefer UNKNOWN over a wrong class".
  * A verdict is a hypothesis about what the post SAYS; it never implies a canonical Event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CLASSIFIER_VERSION = "social-classifier-1"

# claim-type vocabulary (multi-label)
ANNOUNCEMENT = "ANNOUNCEMENT"
TICKETING = "TICKETING"
LINEUP_CHANGE = "LINEUP_CHANGE"
VENUE_CHANGE = "VENUE_CHANGE"
RESCHEDULE = "RESCHEDULE"
CANCELLATION = "CANCELLATION"
SELL_OUT_CLAIM = "SELL_OUT_CLAIM"
ADDITIONAL_SHOW = "ADDITIONAL_SHOW"
PROMOTION = "PROMOTION"
UNKNOWN = "UNKNOWN"

# signal (from extraction) → claim type. postponement folds into the reschedule family.
_SIGNAL_TO_CLASS: dict[str, str] = {
    "announcement": ANNOUNCEMENT,
    "ticketing": TICKETING,
    "sold_out": SELL_OUT_CLAIM,
    "cancellation": CANCELLATION,
    "postponement": RESCHEDULE,
    "reschedule": RESCHEDULE,
    "venue_change": VENUE_CHANGE,
    "lineup_change": LINEUP_CHANGE,
    "additional_show": ADDITIONAL_SHOW,
    "promotion": PROMOTION,
}

# Most-consequential lifecycle first — the primary label when several fire.
_PRIMARY_PRIORITY: tuple[str, ...] = (
    CANCELLATION, RESCHEDULE, VENUE_CHANGE, LINEUP_CHANGE, ADDITIONAL_SHOW,
    SELL_OUT_CLAIM, TICKETING, ANNOUNCEMENT, PROMOTION,
)

# Classes that refer to a concrete event (so, given event identity, can be event-bearing). PROMOTION is
# deliberately excluded — a generic promo/discount is evidence, not an event candidate on its own.
_EVENT_REFERRING = frozenset({
    ANNOUNCEMENT, TICKETING, LINEUP_CHANGE, VENUE_CHANGE, RESCHEDULE, CANCELLATION,
    SELL_OUT_CLAIM, ADDITIONAL_SHOW,
})

# identity fields that count toward "is there a resolvable event here?"
_IDENTITY_FIELDS = ("event_name", "event_date", "event_time", "city", "venue_name", "artists",
                    "organizer", "ticket_url")


@dataclass
class Interpretation:
    """Pure result of classifying one evidence version. Persisted by the service into
    ``social_interpretation`` (this dataclass itself performs no I/O)."""
    claim_types: list[str] = field(default_factory=list)
    primary_claim_type: str | None = None
    interpreted_fields: dict[str, Any] = field(default_factory=dict)
    supporting_evidence: list[str] = field(default_factory=list)
    contradicting_evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    event_bearing: bool = False
    reason_codes: list[str] = field(default_factory=list)
    classifier_version: str = CLASSIFIER_VERSION


def _signals(claims: dict[str, Any]) -> dict[str, bool]:
    """Read the enriched signals map; fall back to the legacy flat ``surface_signals`` list."""
    raw = claims.get("signals")
    if isinstance(raw, dict):
        return {k: bool(v) for k, v in raw.items()}
    legacy = claims.get("surface_signals")
    if isinstance(legacy, list):
        return {str(s): True for s in legacy}
    return {}


def _interpreted_fields(claims: dict[str, Any]) -> dict[str, Any]:
    """Copy only bounded factual/semantic fields into the interpretation. Never the caption."""
    out: dict[str, Any] = {}
    for key in ("event_name", "event_date", "event_time", "city", "venue_name", "ticket_url",
                "organizer"):
        if claims.get(key):
            out[key] = claims[key]
    if isinstance(claims.get("artists"), list) and claims["artists"]:
        out["artists"] = list(claims["artists"])
    if isinstance(claims.get("changes"), dict) and claims["changes"]:
        out["changes"] = claims["changes"]
    return out


def _has_resolvable_identity(fields: dict[str, Any]) -> bool:
    """Enough identity to become an Event candidate: a name, or a date anchored by a venue/artist/city."""
    if fields.get("event_name"):
        return True
    return bool(fields.get("event_date") and (fields.get("venue_name") or fields.get("artists")
                                              or fields.get("city")))


def classify(claims: dict[str, Any]) -> Interpretation:
    """Deterministically classify one evidence version's extracted claims. Pure; idempotent for a given
    input and ``CLASSIFIER_VERSION``."""
    signals = _signals(claims)
    fields = _interpreted_fields(claims)
    negation = bool(claims.get("negation"))
    uncertainty = bool(claims.get("uncertainty"))

    # multi-label: every fired signal maps to a claim type (deduped, stable order)
    claim_types: list[str] = []
    supporting: list[str] = []
    for sig, present in signals.items():
        cls = _SIGNAL_TO_CLASS.get(sig)
        if present and cls and cls not in claim_types:
            claim_types.append(cls)
            supporting.append(f"SIGNAL:{sig}->{cls}")

    contradicting: list[str] = []
    if negation:
        contradicting.append("NEGATION_PRESENT")
    if uncertainty:
        contradicting.append("UNCERTAINTY_PRESENT")

    reason_codes: list[str] = []
    if len(claim_types) > 1:
        reason_codes.append("MULTI_LABEL")
    if SELL_OUT_CLAIM in claim_types:
        reason_codes.append("SELL_OUT_SOURCE_CLAIM_ONLY")  # never verified sell-through

    # primary = most consequential lifecycle among the fired labels
    primary = next((c for c in _PRIMARY_PRIORITY if c in claim_types), None)
    if not claim_types:
        primary = UNKNOWN
        reason_codes.append("NO_SIGNAL_MATCHED")

    # --- event-bearing decision (deterministic) ---
    has_identity = _has_resolvable_identity(fields)
    event_referring = any(c in _EVENT_REFERRING for c in claim_types)
    event_bearing = has_identity and event_referring
    if event_bearing:
        reason_codes.append("EVENT_IDENTITY_RESOLVED")
    else:
        if not has_identity:
            reason_codes.append("NO_RESOLVABLE_EVENT_IDENTITY")
            if uncertainty:
                reason_codes.append("AMBIGUOUS_TEASER")
        if claim_types == [PROMOTION]:
            reason_codes.append("GENERIC_PROMOTION")
        if not event_referring and claim_types:
            reason_codes.append("NON_EVENT_REFERRING")

    # --- confidence (bounded, deterministic): identity coverage + signal presence, penalized by
    # uncertainty. This is a diagnostic score, NOT a probability of truth. ---
    identity_hits = sum(1 for f in _IDENTITY_FIELDS if fields.get(f))
    identity_score = min(identity_hits / 4.0, 1.0)          # 4+ identity fields saturates
    signal_score = 1.0 if claim_types else 0.0
    confidence = round(0.6 * identity_score + 0.4 * signal_score, 3)
    if uncertainty:
        confidence = round(confidence * 0.5, 3)
    if negation:
        confidence = round(confidence * 0.8, 3)

    return Interpretation(
        claim_types=claim_types, primary_claim_type=primary, interpreted_fields=fields,
        supporting_evidence=supporting, contradicting_evidence=contradicting,
        confidence=confidence, event_bearing=event_bearing, reason_codes=reason_codes,
        classifier_version=CLASSIFIER_VERSION)
