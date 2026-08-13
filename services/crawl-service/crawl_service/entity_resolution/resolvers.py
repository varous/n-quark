"""Deterministic entity resolvers (Phase 3.1). Pure, no LLM.

Each resolver takes one source-event's entity evidence plus the entities already known (the source-handle
registry and prior canonical entities, passed in as plain data so the resolvers stay pure/testable) and
returns an explainable decision: a status, a canonical id (or none), the supporting/contradicting signals
and a reason code. Ambiguous identities never auto-resolve; generic names never collapse without
geography/organizer evidence; a tribute act never resolves to the original artist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from crawl_service.entity_resolution import normalizers as N
from crawl_service.entity_resolution.evidence import (
    ARTIST,
    EVENT_SERIES,
    ORGANIZER,
    VENUE,
    EntityEvidence,
)

RESOLVER_VERSION = "entity-resolver-1"

# statuses
RESOLVED = "RESOLVED"
POSSIBLE_MATCH = "POSSIBLE_MATCH"
AMBIGUOUS = "AMBIGUOUS"
UNRESOLVED = "UNRESOLVED"
REJECTED = "REJECTED"
# Phase 5B.2.4 — interpretation-gate states (no migration: values in the existing status column).
REVIEW_REQUIRED = "REVIEW_REQUIRED"   # flagged by interpretation; no canonical mutation until reviewed
ROLE_CONFLICT = "ROLE_CONFLICT"       # identity exists as a different canonical type (cross-type)
PLACEHOLDER = "PLACEHOLDER"           # an absence marker; never a canonical
QUARANTINED = "QUARANTINED"           # an existing bad canonical suppressed from product (evidence kept)
# states that must NEVER carry a canonical into product surfaces
NON_PRODUCT_STATES = frozenset({REVIEW_REQUIRED, ROLE_CONFLICT, PLACEHOLDER, QUARANTINED})

# reason codes
SOURCE_HANDLE_MATCH = "SOURCE_HANDLE_MATCH"
EXACT_UNIQUE_ALIAS = "EXACT_UNIQUE_ALIAS"
NAME_AND_CITY_MATCH = "NAME_AND_CITY_MATCH"
NAME_AND_ADDRESS_MATCH = "NAME_AND_ADDRESS_MATCH"
OFFICIAL_URL_MATCH = "OFFICIAL_URL_MATCH"
GENERIC_NAME = "GENERIC_NAME"
AMBIGUOUS_NAME = "AMBIGUOUS_NAME"
MULTIPLE_CANDIDATES = "MULTIPLE_CANDIDATES"
CITY_CONFLICT = "CITY_CONFLICT"
ADDRESS_CONFLICT = "ADDRESS_CONFLICT"
TRIBUTE_OR_COVER_ACT = "TRIBUTE_OR_COVER_ACT"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
VENUE_HAS_NO_GEOGRAPHY = "VENUE_HAS_NO_GEOGRAPHY"
NEW_CANONICAL_ENTITY_CREATED = "NEW_CANONICAL_ENTITY_CREATED"


@dataclass
class ResolutionResult:
    status: str
    canonical_entity_id: str | None
    score: float
    reason_code: str
    supporting: list[str] = field(default_factory=list)
    contradicting: list[str] = field(default_factory=list)
    created_new: bool = False
    resolver_version: str = RESOLVER_VERSION


@dataclass
class KnownEntities:
    """Entities already known to the resolver, sourced from crawl-service's own tables (not a graph
    scan): the handle registry and prior RESOLVED canonicals indexed for candidate lookup."""

    # (source, source_entity_handle) -> canonical_entity_id
    handle_map: dict[tuple[str, str], str] = field(default_factory=dict)
    # normalized_name -> set of canonical ids (artists / organizers / series)
    name_map: dict[str, set[str]] = field(default_factory=dict)
    # normalized_venue_name -> list of (canonical_id, city)
    venue_map: dict[str, list[tuple[str, str | None]]] = field(default_factory=dict)


def _cid(entity_type: str, *parts: str) -> str:
    tail = "--".join(N.slug(p) for p in parts if p)
    return f"{entity_type}:{tail}"


# ================================================================================ ARTIST
def resolve_artist(ev: EntityEvidence, known: KnownEntities) -> ResolutionResult:
    handle_key = (ev.source, ev.source_entity_handle)
    if handle_key in known.handle_map:
        return ResolutionResult(RESOLVED, known.handle_map[handle_key], 0.99, SOURCE_HANDLE_MATCH,
                                supporting=["SOURCE_HANDLE_MATCH"])

    name = ev.normalized_name
    if not name:
        return ResolutionResult(UNRESOLVED, None, 0.0, INSUFFICIENT_EVIDENCE)

    # A tribute/cover act carries the marker in its normalized identity, so its canonical id can never
    # equal the original artist's — it resolves to its own entity, never collapses into the original.
    if ev.evidence.get("is_tribute"):
        cid = _cid("artist", name)
        existing = cid in known.name_map.get(name, set())
        return ResolutionResult(
            RESOLVED, cid, 0.8, EXACT_UNIQUE_ALIAS if existing else NEW_CANONICAL_ENTITY_CREATED,
            supporting=["TRIBUTE_ENTITY"], contradicting=[TRIBUTE_OR_COVER_ACT],
            created_new=not existing)

    if ev.evidence.get("is_ambiguous"):
        candidates = known.name_map.get(name, set())
        return ResolutionResult(AMBIGUOUS, None, 0.4, AMBIGUOUS_NAME,
                                contradicting=["AMBIGUOUS_ARTIST_NAME"],
                                supporting=list(candidates)[:3])

    cid = _cid("artist", name)
    if cid in known.name_map.get(name, set()):
        return ResolutionResult(RESOLVED, cid, 0.9, EXACT_UNIQUE_ALIAS, supporting=["EXACT_NAME_MATCH"])
    return ResolutionResult(RESOLVED, cid, 0.85, NEW_CANONICAL_ENTITY_CREATED,
                            supporting=["NON_GENERIC_NAME"], created_new=True)


# ================================================================================ VENUE
def resolve_venue(ev: EntityEvidence, known: KnownEntities) -> ResolutionResult:
    handle_key = (ev.source, ev.source_entity_handle)
    if handle_key in known.handle_map:
        return ResolutionResult(RESOLVED, known.handle_map[handle_key], 0.99, SOURCE_HANDLE_MATCH,
                                supporting=["SOURCE_HANDLE_MATCH"])

    name = ev.normalized_name
    if not name:
        return ResolutionResult(UNRESOLVED, None, 0.0, INSUFFICIENT_EVIDENCE)
    generic = bool(ev.evidence.get("is_generic"))
    city = ev.evidence.get("city")
    existing = known.venue_map.get(name, [])
    same_city = [cid for cid, c in existing if c and city and N.slug(c) == N.slug(city)]
    diff_city_only = bool(existing) and not same_city and bool(city) \
        and all(c and N.slug(c) != N.slug(city) for _, c in existing)

    if generic and not city:
        return ResolutionResult(AMBIGUOUS, None, 0.3, GENERIC_NAME,
                                contradicting=["GENERIC_VENUE_NO_GEOGRAPHY"])
    if not city:
        # A named but geography-less venue can't be confidently pinned; keep it resolvable later.
        if existing:
            return ResolutionResult(POSSIBLE_MATCH, existing[0][0], 0.45, MULTIPLE_CANDIDATES,
                                    contradicting=["NO_CITY_TO_DISAMBIGUATE"])
        return ResolutionResult(UNRESOLVED, None, 0.2, VENUE_HAS_NO_GEOGRAPHY)

    cid = _cid("venue", name, city)
    if same_city:
        status = POSSIBLE_MATCH if generic else RESOLVED
        return ResolutionResult(status, same_city[0], 0.7 if generic else 0.92, NAME_AND_CITY_MATCH,
                                supporting=["NAME_AND_CITY_MATCH"])
    supporting = ["DIFFERENT_LOCATION"] if diff_city_only else ["NAME_AND_CITY"]
    if generic:
        return ResolutionResult(POSSIBLE_MATCH, cid, 0.5, GENERIC_NAME,
                                supporting=supporting, contradicting=["GENERIC_VENUE_NAME"],
                                created_new=True)
    return ResolutionResult(RESOLVED, cid, 0.85, NEW_CANONICAL_ENTITY_CREATED,
                            supporting=supporting, created_new=True)


# ================================================================================ ORGANIZER
_GENERIC_ORGANIZERS = frozenset({"events", "productions", "entertainment", "the team", "management"})


def resolve_organizer(ev: EntityEvidence, known: KnownEntities) -> ResolutionResult:
    handle_key = (ev.source, ev.source_entity_handle)
    if handle_key in known.handle_map:
        return ResolutionResult(RESOLVED, known.handle_map[handle_key], 0.99, SOURCE_HANDLE_MATCH,
                                supporting=["SOURCE_HANDLE_MATCH"])
    name = ev.normalized_name
    if not name:
        return ResolutionResult(UNRESOLVED, None, 0.0, INSUFFICIENT_EVIDENCE)
    if name in _GENERIC_ORGANIZERS or len(name) <= 2:
        return ResolutionResult(AMBIGUOUS, None, 0.3, AMBIGUOUS_NAME,
                                contradicting=["GENERIC_ORGANIZER_NAME"])
    cid = _cid("organizer", name)
    if cid in known.name_map.get(name, set()):
        return ResolutionResult(RESOLVED, cid, 0.85, EXACT_UNIQUE_ALIAS, supporting=["EXACT_NAME_MATCH"])
    return ResolutionResult(RESOLVED, cid, 0.75, NEW_CANONICAL_ENTITY_CREATED,
                            supporting=["NON_GENERIC_NAME"], created_new=True)


# ================================================================================ EVENT SERIES
def resolve_series(ev: EntityEvidence, known: KnownEntities) -> ResolutionResult:
    handle_key = (ev.source, ev.source_entity_handle)
    if handle_key in known.handle_map:
        return ResolutionResult(RESOLVED, known.handle_map[handle_key], 0.9, SOURCE_HANDLE_MATCH,
                                supporting=["SOURCE_HANDLE_MATCH"])
    name = ev.normalized_name
    if not name:
        return ResolutionResult(UNRESOLVED, None, 0.0, INSUFFICIENT_EVIDENCE)
    organizer = ev.evidence.get("organizer")
    generic = bool(ev.evidence.get("is_generic"))
    if generic and not organizer:
        return ResolutionResult(AMBIGUOUS, None, 0.3, GENERIC_NAME,
                                contradicting=["GENERIC_SERIES_TITLE"])
    # Organizer disambiguates the series identity: same title + different organizer must NOT link.
    cid = _cid("series", name, organizer) if organizer else _cid("series", name)
    known_ids = known.name_map.get(name, set())
    linked = cid in known_ids
    status = POSSIBLE_MATCH if generic else RESOLVED
    reason = EXACT_UNIQUE_ALIAS if linked else NEW_CANONICAL_ENTITY_CREATED
    supporting = ["SERIES_TITLE_AND_ORGANIZER"] if organizer else ["SERIES_TITLE"]
    return ResolutionResult(status, cid, 0.7 if generic else (0.8 if linked else 0.75), reason,
                            supporting=supporting, created_new=not linked)


RESOLVERS = {ARTIST: resolve_artist, VENUE: resolve_venue,
             ORGANIZER: resolve_organizer, EVENT_SERIES: resolve_series}


def resolve(ev: EntityEvidence, known: KnownEntities) -> ResolutionResult:
    return RESOLVERS[ev.entity_type](ev, known)
