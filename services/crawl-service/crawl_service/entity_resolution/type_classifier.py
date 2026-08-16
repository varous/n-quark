"""Deterministic, explainable entity-type evidence classifier.

The requested source role is strong evidence, but not authority. Weighted independent signals can
route a contradictory mention to review before any canonical is created. Vocabulary terms never
directly create or retype an entity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from crawl_service.entity_resolution.normalizers import slug

ENTITY_TYPES = ("ARTIST", "VENUE", "ORGANIZER")
CLEAR_TYPE = "CLEAR_TYPE"
ROLE_CONFLICT = "ROLE_CONFLICT"
AMBIGUOUS_TYPE = "AMBIGUOUS_TYPE"
UNKNOWN = "UNKNOWN"

VOCABULARY = {
    "ARTIST": {
        "strong": ("performing artist", "music artist", "live act", "singer", "vocalist", "musician", "rapper", "composer", "instrumentalist", "comedian", "comic"),
        "weak": ("artist", "performer", "band", "duo", "trio", "quartet", "ensemble", "dj", "actor", "act", "orchestra"),
    },
    "VENUE": {
        "strong": ("auditorium", "stadium", "amphitheatre", "amphitheater", "banquet hall", "convention centre", "convention center", "exhibition centre", "exhibition center", "community hall", "performance space", "music club", "live club"),
        "weak": ("venue", "hall", "arena", "theatre", "theater", "club", "cafe", "coffee house", "pub", "bar", "lounge", "hotel", "resort", "banquet", "grounds", "ground", "lawn", "rooftop", "mall", "centre", "center"),
    },
    "ORGANIZER": {
        "strong": ("event company", "private limited", "pvt ltd", "production house", "event organisers", "event organizers", "organised by", "organized by"),
        "weak": ("organizer", "organiser", "promoter", "presenter", "host", "producer", "events", "entertainment", "productions", "production", "media", "management", "agency", "foundation", "association", "society", "trust", "council", "chamber", "academy", "collective", "company", "llp", "ltd", "limited", "network", "business", "promotions", "helpline"),
    },
}

_SCHEMA = {"person": "ARTIST", "performinggroup": "ARTIST", "musicgroup": "ARTIST",
           "place": "VENUE", "organization": "ORGANIZER"}


@dataclass(frozen=True)
class TypeClassification:
    requested_role: str
    predicted_type: str
    outcome: str
    confidence: str
    scores: dict[str, float]
    supporting: list[str] = field(default_factory=list)
    contradicting: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"requested_role": self.requested_role, "predicted_type": self.predicted_type,
                "outcome": self.outcome, "confidence": self.confidence, "scores": self.scores,
                "supporting": self.supporting, "contradicting": self.contradicting}


def _phrases(raw: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip() + " "


def classify_type(*, raw: str, requested_role: str, source_field: str | None = None,
                  schema_type: str | None = None, existing_types: set[str] | None = None,
                  cohort_roles: set[str] | None = None, operator_confirmed_types: set[str] | None = None,
                  is_address_context: bool = False) -> TypeClassification:
    scores = {t: 0.0 for t in ENTITY_TYPES}
    reasons: dict[str, list[str]] = {t: [] for t in ENTITY_TYPES}
    requested_role = requested_role.upper()
    if requested_role in scores:
        scores[requested_role] += 4
        reasons[requested_role].append(f"source_role:{source_field or requested_role.lower()}")
    schema = _SCHEMA.get(str(schema_type or "").lower().replace("https://schema.org/", ""))
    if schema:
        scores[schema] += 5
        reasons[schema].append(f"structured_type:{schema_type}")
    for role in existing_types or set():
        if role in scores:
            scores[role] += 7
            reasons[role].append(f"existing_canonical:{role}")
    for role in cohort_roles or set():
        if role in scores and role != requested_role:
            scores[role] += 7
            reasons[role].append(f"same_event_role:{role}")
    for role in operator_confirmed_types or set():
        if role in scores:
            scores[role] += 8
            reasons[role].append(f"operator_confirmed:{role}")
    text = _phrases(raw)
    for role, families in VOCABULARY.items():
        strong = [p for p in families["strong"] if f" {p} " in text]
        weak = [p for p in families["weak"] if f" {p} " in text]
        if strong:
            scores[role] += min(5, 3 + len(strong) - 1)
            reasons[role].append("strong_terms:" + ",".join(strong))
        if weak:
            scores[role] += min(3, len(weak))
            reasons[role].append("weak_terms:" + ",".join(weak))
    org_shape = sum(1 for p in VOCABULARY["ORGANIZER"]["weak"] if f" {p} " in text)
    if org_shape >= 2:
        scores["ORGANIZER"] += 2
        scores["ARTIST"] -= 2
        reasons["ORGANIZER"].append("organization_name_shape")
    if is_address_context:
        scores["VENUE"] += 4
        scores["ARTIST"] -= 2
        reasons["VENUE"].append("location_or_address_context")
    ranked = sorted(scores, key=lambda t: scores[t], reverse=True)
    top, second = ranked[0], ranked[1]
    margin = scores[top] - scores[second]
    if scores[top] <= 0:
        outcome, predicted, confidence = UNKNOWN, "UNKNOWN", "LOW"
    elif top != requested_role and margin >= 3:
        outcome, predicted, confidence = ROLE_CONFLICT, top, "HIGH"
    elif top != requested_role or margin < 2:
        outcome, predicted, confidence = AMBIGUOUS_TYPE, (top if margin > 0 else "UNKNOWN"), "LOW"
    else:
        outcome, predicted, confidence = CLEAR_TYPE, top, "HIGH" if margin >= 4 else "MEDIUM"
    support = reasons.get(top, [])
    contradict = [r for role in ENTITY_TYPES if role != top for r in reasons[role]]
    return TypeClassification(requested_role, predicted, outcome, confidence, scores, support, contradict)
