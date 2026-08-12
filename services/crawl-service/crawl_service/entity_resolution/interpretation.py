"""Role-aware mention interpretation (Phase 5B.2.3).

Sits between raw mention evidence and canonical candidate generation. Given a raw source mention plus its
EXPECTED ROLE (from the source field) and the existing canonical context, it decides — deterministically
first, AI only if enabled and needed — whether the mention should:

- be suppressed as a PLACEHOLDER (absence marker, never a canonical);
- be split into several mentions (COMPOUND_SPLIT);
- be held for review because its type conflicts with an existing canonical (ROLE_CONFLICT /
  CROSS_TYPE_CONFLICT) or its compound structure is ambiguous (REVIEW_REQUIRED);
- or pass through unchanged to normal resolution (OK).

Order follows the cost/control policy: placeholder → compound (deterministic) → cross-type → (AI only if
still ambiguous) → review. The source role is preserved as evidence throughout. Canonical creation is
NEVER performed here — this only interprets and routes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from crawl_service.entity_resolution import compound as C
from crawl_service.entity_resolution import normalizers as N
from crawl_service.entity_resolution import placeholders as P

# interpretation outcomes (routing states — distinct from the resolution status enum)
OK = "OK"
PLACEHOLDER = "PLACEHOLDER"
COMPOUND_SPLIT = "COMPOUND_SPLIT"
ROLE_CONFLICT = "ROLE_CONFLICT"
CROSS_TYPE_CONFLICT = "CROSS_TYPE_CONFLICT"
REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class Interpretation:
    outcome: str
    expected_role: str
    raw: str
    normalized: str = ""
    parts: list[str] = field(default_factory=list)          # for COMPOUND_SPLIT
    conflict_types: list[str] = field(default_factory=list)  # for CROSS_TYPE_CONFLICT
    reason: str = ""
    confidence: float = 0.0
    ai_assisted: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)


def _norm_for(role: str, raw: str) -> str:
    if role == "VENUE":
        return N.normalize_venue(raw).normalized
    if role == "ORGANIZER":
        return N.normalize_organizer(raw).normalized
    return N.normalize_artist(raw).normalized


def interpret_mention(*, raw: str, expected_role: str,
                      known_by_type: dict[str, frozenset[str]] | None = None,
                      cross_type_index: dict[str, set[str]] | None = None,
                      adjudicator: Any | None = None) -> Interpretation:
    """Interpret one raw mention. Pure + deterministic unless ``adjudicator`` is enabled AND the case is
    still ambiguous. ``known_by_type`` maps a role → normalized known canonical names (for compound
    corroboration); ``cross_type_index`` maps a normalized name → the set of canonical types it already
    exists as (for cross-type conflict)."""
    known_by_type = known_by_type or {}
    cross_type_index = cross_type_index or {}
    normalized = _norm_for(expected_role, raw)
    base = {"expected_role": expected_role, "raw": raw, "normalized": normalized,
            "source_role_preserved": True}

    # 1) placeholder / absence — never a canonical, raw preserved by the caller.
    ph = P.classify_placeholder(raw)
    if ph.is_placeholder:
        return Interpretation(PLACEHOLDER, expected_role, raw, normalized,
                              reason=f"placeholder:{ph.reason}", confidence=0.95,
                              evidence={**base, "placeholder": ph.reason, "matched": ph.matched})

    # 2) compound (deterministic first). Only artists are meaningfully compound in this pass.
    if expected_role == "ARTIST":
        known = known_by_type.get("ARTIST", frozenset())
        comp = C.parse_compound(raw, known_names=known)
        if comp.kind == C.COMPOUND:
            return Interpretation(COMPOUND_SPLIT, expected_role, raw, normalized, parts=comp.parts,
                                  reason=f"compound:{comp.reason}", confidence=comp.confidence,
                                  evidence={**base, "compound_reason": comp.reason})
        if comp.kind == C.AMBIGUOUS:
            # deterministic evidence could not decide — AI only if enabled, else review.
            adj = adjudicator
            if adj is not None and getattr(adj, "available", False):
                # (kept synchronous-free: adjudicator integration point; disabled in this pass)
                pass
            return Interpretation(REVIEW_REQUIRED, expected_role, raw, normalized, parts=comp.parts,
                                  reason=f"ambiguous_compound:{comp.reason}", confidence=comp.confidence,
                                  evidence={**base, "compound_reason": comp.reason,
                                            "needs": "split adjudication or operator review"})

    # 3) cross-type / role conflict — the same identity already exists as a DIFFERENT canonical type.
    # Keyed on a neutral slug (role-specific normalizers strip different tails, e.g. artist "Live"),
    # so the caller must build cross_type_index with N.slug(name) too.
    cross_key = N.slug(raw)
    other_types = sorted(t for t in cross_type_index.get(cross_key, set()) if t != expected_role)
    if other_types and cross_key:
        outcome = ROLE_CONFLICT if expected_role in ("ARTIST", "VENUE", "ORGANIZER") else CROSS_TYPE_CONFLICT
        return Interpretation(CROSS_TYPE_CONFLICT, expected_role, raw, normalized, conflict_types=other_types,
                              reason=f"exists_as:{','.join(other_types)}", confidence=0.7,
                              evidence={**base, "detected_conflict_with": other_types,
                                        "outcome_kind": outcome})

    # 4) nothing suppressive — pass through to the existing deterministic resolver.
    return Interpretation(OK, expected_role, raw, normalized, reason="no_interpretation_flag",
                          confidence=1.0, evidence=base)
