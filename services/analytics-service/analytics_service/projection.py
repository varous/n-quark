"""Canonical query projection (Phase 4A).

A reusable, deterministic read-layer canonicalizer. Given an entity id and the supersession / alias
relationships observed in the graph, it resolves to the *canonical* entity used for aggregation — so a
legacy naive-projection node and its evidence-resolved canonical are never double-counted.

It is **non-destructive**: it only reads relationship maps; it never migrates or deletes graph nodes.
Source and legacy ids are preserved in the resolution path so every fold is explainable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Relationship precedence when following folds: a legacy node is *superseded_by* its canonical
# (Phase B, ADMIN-only), and an alias handle *identifies* a canonical (Phase 3.1). Supersession wins.
MAX_FOLD_HOPS = 16


@dataclass(frozen=True)
class Canonicalization:
    input_entity_id: str
    canonical_entity_id: str
    resolution_path: list[str]
    identity_state: str
    warnings: list[str] = field(default_factory=list)

    @property
    def folded(self) -> bool:
        return self.canonical_entity_id != self.input_entity_id


def canonicalize(
    entity_id: str,
    *,
    supersession: dict[str, str],
    alias: dict[str, str] | None = None,
    identity_states: dict[str, str] | None = None,
    known_ids: set[str] | None = None,
    max_hops: int = MAX_FOLD_HOPS,
) -> Canonicalization:
    """Resolve `entity_id` to its canonical id by following SUPERSEDED_BY then alias edges.

    - `supersession`: legacy_id -> canonical_id (the SUPERSEDED_BY target).
    - `alias`: alias_id -> canonical_id (a supported alias fold, e.g. IDENTIFIES). Optional.
    - `identity_states`: id -> identity_state, used to label the resolved canonical.
    - `known_ids`: the set of ids that actually exist; a fold to an unknown id raises a warning.

    Cycles and over-long chains are detected and stopped (with a warning) rather than looping.
    """
    alias = alias or {}
    identity_states = identity_states or {}
    warnings: list[str] = []
    path: list[str] = [entity_id]
    seen: set[str] = {entity_id}
    current = entity_id

    for _ in range(max_hops):
        nxt = supersession.get(current)
        if nxt is None:
            nxt = alias.get(current)
        if nxt is None:
            break
        if nxt == current:
            warnings.append(f"self-referential supersession at {current!r}; stopping")
            break
        if nxt in seen:
            warnings.append(
                f"supersession cycle detected: {' -> '.join(path)} -> {nxt}; stopping at {current!r}"
            )
            break
        path.append(nxt)
        seen.add(nxt)
        current = nxt
    else:
        # loop exhausted without break => still had a next hop available
        if supersession.get(current) or alias.get(current):
            warnings.append(
                f"supersession chain exceeded {max_hops} hops; stopping at {current!r} (possible invalid chain)"
            )

    if known_ids is not None and current not in known_ids:
        warnings.append(f"canonical target {current!r} not found among known entities")

    identity_state = identity_states.get(current, "UNKNOWN")
    return Canonicalization(
        input_entity_id=entity_id,
        canonical_entity_id=current,
        resolution_path=path,
        identity_state=identity_state,
        warnings=warnings,
    )


class Canonicalizer:
    """Bound canonicalizer over one loaded snapshot — caches results for stable, cheap reuse."""

    def __init__(
        self,
        *,
        supersession: dict[str, str],
        alias: dict[str, str] | None = None,
        identity_states: dict[str, str] | None = None,
        known_ids: set[str] | None = None,
    ) -> None:
        self._supersession = dict(supersession)
        self._alias = dict(alias or {})
        self._identity_states = dict(identity_states or {})
        self._known_ids = set(known_ids) if known_ids is not None else None
        self._cache: dict[str, Canonicalization] = {}

    def resolve(self, entity_id: str) -> Canonicalization:
        hit = self._cache.get(entity_id)
        if hit is None:
            hit = canonicalize(
                entity_id,
                supersession=self._supersession,
                alias=self._alias,
                identity_states=self._identity_states,
                known_ids=self._known_ids,
            )
            self._cache[entity_id] = hit
        return hit

    def canonical_id(self, entity_id: str) -> str:
        return self.resolve(entity_id).canonical_entity_id

    def dedupe(self, entity_ids: list[str]) -> list[str]:
        """Fold a list of ids to their canonical ids, de-duplicated, order-stable by first appearance."""
        out: list[str] = []
        seen: set[str] = set()
        for eid in entity_ids:
            cid = self.canonical_id(eid)
            if cid not in seen:
                seen.add(cid)
                out.append(cid)
        return out

    @property
    def superseded_folds(self) -> list[Canonicalization]:
        """Every legacy id that folds to a different canonical (for dedup trace)."""
        return [self.resolve(legacy) for legacy in sorted(self._supersession)]
