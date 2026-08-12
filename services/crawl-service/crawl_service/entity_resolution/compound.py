"""Deterministic compound-mention parser (Phase 5B.2.3).

A source string like "Arijit Singh, Shreya Ghoshal" may encode several artists — but a delimiter is NOT
proof of a compound (legitimate names contain "&", "+", commas: "Salt-N-Pepa", "Earth, Wind & Fire",
"Simon & Garfunkel"). This staged parser prefers deterministic evidence (an exact known-canonical/alias
match on the whole string, or on every split part) and, when the evidence cannot decide, returns
AMBIGUOUS_COMPOUND for review/adjudication rather than splitting arbitrarily. No LLM here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from crawl_service.entity_resolution import normalizers as N

SINGLE = "SINGLE_ENTITY"
COMPOUND = "COMPOUND_ENTITY"
AMBIGUOUS = "AMBIGUOUS_COMPOUND"

# split points: comma / semicolon / slash, and spaced &, +, x, vs, feat., ft., featuring, "and"
_DELIM = re.compile(
    r"\s*[,;/]\s*|\s+(?:&|\+|x|vs\.?|feat\.?|ft\.?|featuring|and)\s+", re.IGNORECASE)
_HAS_DELIM = re.compile(r"[,;/]|\s(?:&|\+|x|vs\.?|feat\.?|ft\.?|featuring|and)\s", re.IGNORECASE)


@dataclass(frozen=True)
class CompoundResult:
    kind: str                       # SINGLE_ENTITY | COMPOUND_ENTITY | AMBIGUOUS_COMPOUND
    parts: list[str] = field(default_factory=list)   # raw parts when split-worthy (original casing)
    reason: str = ""
    confidence: float = 0.0


def _split(raw: str) -> list[str]:
    return [p.strip() for p in _DELIM.split(raw) if p and p.strip()]


def parse_compound(raw: str, *, known_names: frozenset[str] = frozenset(),
                   known_aliases: frozenset[str] = frozenset()) -> CompoundResult:
    """Classify a raw mention as a single entity, a confident compound (with parts), or ambiguous.

    ``known_names`` / ``known_aliases`` are NORMALIZED existing canonical names for the expected type —
    the deterministic corroboration that protects band names and confirms real multi-artist strings."""
    raw = (raw or "").strip()
    if not raw:
        return CompoundResult(SINGLE, reason="empty")
    known = known_names | known_aliases
    whole = N.normalize_artist(raw).normalized

    # 1) the WHOLE string is a known canonical/alias → single entity, never split (band-name protection).
    if whole and whole in known:
        return CompoundResult(SINGLE, reason="whole_matches_known_canonical", confidence=1.0)

    if not _HAS_DELIM.search(raw):
        return CompoundResult(SINGLE, reason="no_delimiter", confidence=0.9)

    parts = _split(raw)
    if len(parts) < 2:
        return CompoundResult(SINGLE, reason="delimiter_but_single_part", confidence=0.8)

    norm_parts = [N.normalize_artist(p).normalized for p in parts]
    matched = [np in known for np in norm_parts if np]
    n_known = sum(1 for m in matched if m)

    # 2) every part is a known canonical/alias → confident compound.
    if norm_parts and all(np and np in known for np in norm_parts):
        return CompoundResult(COMPOUND, parts=parts, reason="all_parts_known_canonical", confidence=0.95)

    # 3) most parts known (≥2 and ≥ half) → confident-enough compound.
    if n_known >= 2 and n_known >= len([p for p in norm_parts if p]) / 2:
        return CompoundResult(COMPOUND, parts=parts,
                              reason=f"{n_known}/{len(parts)} parts are known canonicals", confidence=0.8)

    # 4) a comma-separated list of ≥3 plausible name-shaped parts is very likely a lineup list.
    comma_list = "," in raw and len(parts) >= 3 and all(1 <= len(p.split()) <= 5 for p in parts)
    if comma_list:
        return CompoundResult(COMPOUND, parts=parts, reason="comma_separated_lineup_list", confidence=0.7)

    # 5) delimiter present but no deterministic corroboration → ambiguous (review / AI adjudication).
    return CompoundResult(AMBIGUOUS, parts=parts, reason="delimiter_without_corroboration", confidence=0.4)
