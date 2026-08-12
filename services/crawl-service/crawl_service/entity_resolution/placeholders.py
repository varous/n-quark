"""Deterministic placeholder / absence classifier (Phase 5B.2.3).

A source string like "Venue to be announced" is an ABSENCE marker, not an entity — it must never become a
canonical Venue/Artist/Organizer. This is pure, conservative pattern matching: it fires only on strings
that are essentially a placeholder token (optionally prefixed by the entity kind, e.g. "Venue TBA"), never
on a legitimate named entity that merely contains a common word ("The Coming Soon Collective" is a real
name, not a placeholder). No LLM, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Canonical placeholder phrases (normalized: lowercased, punctuation→space, collapsed). Order-independent.
_PLACEHOLDER_PHRASES = frozenset({
    "tba", "tbd", "tbc", "n a", "na", "none", "null", "nil",
    "to be announced", "to be confirmed", "to be decided", "to be determined",
    "yet to be announced", "yet to be confirmed", "not announced", "not yet announced",
    "not confirmed", "coming soon", "announcing soon", "details coming soon",
    "unknown", "unnamed", "no venue", "no artist", "no organizer", "no organiser",
    "venue to be announced", "artist to be announced", "lineup to be announced",
    "various", "various artists", "multiple", "others", "and more", "more tba",
})

# Leading entity-kind words that may prefix a placeholder ("Venue TBA", "Artist: TBA").
_KIND_PREFIX = re.compile(
    r"^(venue|artist|artists|performer|performers|lineup|line up|line-up|act|acts|"
    r"organizer|organiser|promoter|host|location|place)\s*[:\-]?\s*", re.IGNORECASE)

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def _norm(value: str) -> str:
    v = _PUNCT.sub(" ", (value or "").lower())
    return _WS.sub(" ", v).strip()


@dataclass(frozen=True)
class PlaceholderResult:
    is_placeholder: bool
    reason: str | None = None
    matched: str | None = None


def classify_placeholder(raw: str | None) -> PlaceholderResult:
    """Return whether ``raw`` is an absence/placeholder marker (conservative). The original string is the
    caller's to preserve — this only classifies, never rewrites."""
    if raw is None:
        return PlaceholderResult(True, reason="empty")
    norm = _norm(raw)
    if not norm:
        return PlaceholderResult(True, reason="empty")

    if norm in _PLACEHOLDER_PHRASES:
        return PlaceholderResult(True, reason="exact_placeholder_phrase", matched=norm)

    # strip a leading kind word and re-test ("Venue to be announced" → "to be announced")
    stripped = _KIND_PREFIX.sub("", raw, count=1)
    snorm = _norm(stripped)
    if snorm and snorm != norm and snorm in _PLACEHOLDER_PHRASES:
        return PlaceholderResult(True, reason="kind_prefixed_placeholder", matched=snorm)

    # a bare "na"/"tba"-style token surrounded by nothing meaningful
    if norm in {"na", "n a", "tba", "tbd", "tbc"}:
        return PlaceholderResult(True, reason="short_placeholder_token", matched=norm)

    return PlaceholderResult(False)
