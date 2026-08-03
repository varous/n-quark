"""Authoritative field registry + candidate model for Phase 2.1 enrichment.

One place defines, per supported field: its normalizer, which evidence source types may supply it,
their precedence (authority), the minimum confidence to auto-resolve, whether derived (canonical
relationship / temporal) resolution is allowed, and whether the field feeds the scheduler.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

RESOLVER_VERSION = "enrichment-resolver-1"

# --- source types (evidence surfaces) ------------------------------------------------------------
SOURCE_API = "SOURCE_API"
SOURCE_PUBLIC_PAGE = "SOURCE_PUBLIC_PAGE"
JSON_LD = "JSON_LD"
EMBEDDED_STATE = "EMBEDDED_STATE"
OPEN_GRAPH = "OPEN_GRAPH"
VISIBLE_TEXT = "VISIBLE_TEXT"
CANONICAL_ENTITY_RELATIONSHIP = "CANONICAL_ENTITY_RELATIONSHIP"
TEMPORAL_OBSERVATION = "TEMPORAL_OBSERVATION"
SOURCE_TYPES = frozenset({
    SOURCE_API, SOURCE_PUBLIC_PAGE, JSON_LD, EMBEDDED_STATE, OPEN_GRAPH, VISIBLE_TEXT,
    CANONICAL_ENTITY_RELATIONSHIP, TEMPORAL_OBSERVATION,
})

# --- extraction methods --------------------------------------------------------------------------
DIRECT_FIELD = "DIRECT_FIELD"
STRUCTURED_DATA = "STRUCTURED_DATA"
PAGE_METADATA = "PAGE_METADATA"
TEXT_PARSE = "TEXT_PARSE"
VENUE_RELATIONSHIP = "VENUE_RELATIONSHIP"
TEMPORAL_INTERVAL = "TEMPORAL_INTERVAL"

OBSERVED_PUBLIC_STATE = "observed_public_state"

# --- source-family / independence semantics (Phase 2.2) ------------------------------------------
# (surface, source_family, independence_group) per source type. Multiple Boshow-derived surfaces
# (API, share page, JSON-LD, OG, visible text, and the graph projection built from the Boshow
# ingest) share ONE independence group — they are NOT independent confirmation of each other.
# Only a genuinely different independence group (e.g. n-quark's own temporal observation) counts
# as independent consensus.
_SURFACE_META: dict[str, tuple[str, str, str]] = {
    SOURCE_API: ("api", "boshow", "boshow_origin"),
    SOURCE_PUBLIC_PAGE: ("public_page", "boshow", "boshow_origin"),
    JSON_LD: ("public_json_ld", "boshow", "boshow_origin"),
    EMBEDDED_STATE: ("embedded_state", "boshow", "boshow_origin"),
    OPEN_GRAPH: ("open_graph", "boshow", "boshow_origin"),
    VISIBLE_TEXT: ("visible_text", "boshow", "boshow_origin"),
    CANONICAL_ENTITY_RELATIONSHIP: ("venue_relationship", "n_quark_graph", "boshow_origin"),
    TEMPORAL_OBSERVATION: ("temporal", "n_quark_observation", "nquark_temporal"),
}


def surface_meta(source_type: str) -> tuple[str, str, str]:
    return _SURFACE_META.get(source_type, ("unknown", "unknown", "unknown"))


# --- normalizers ---------------------------------------------------------------------------------
def _norm_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _norm_id(v: Any) -> str | None:
    return _norm_str(v)


def _norm_dt(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    s = str(v).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).isoformat()  # handles trailing 'Z' (py3.11+)
    except ValueError:
        return None  # unparseable -> NOT a candidate (caller treats as extraction failure)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    normalizer: Any
    precedence: tuple[str, ...]          # source types, highest authority first
    min_auto_confidence: float = 0.6
    derived_allowed: bool = False        # canonical-relationship / temporal resolution permitted
    scheduler_relevant: bool = False

    def authority(self, source_type: str) -> int:
        """Lower = higher authority. Unknown source types rank last."""
        try:
            return self.precedence.index(source_type)
        except ValueError:
            return len(self.precedence) + 1


FIELD_REGISTRY: dict[str, FieldSpec] = {
    "starts_at": FieldSpec(
        "starts_at", _norm_dt,
        (SOURCE_API, JSON_LD, EMBEDDED_STATE, OPEN_GRAPH, VISIBLE_TEXT),
        min_auto_confidence=0.6, scheduler_relevant=True),
    "end_at": FieldSpec(
        "end_at", _norm_dt, (SOURCE_API, JSON_LD, EMBEDDED_STATE), min_auto_confidence=0.6),
    "venue_name": FieldSpec(
        "venue_name", _norm_str,
        (SOURCE_API, JSON_LD, EMBEDDED_STATE, OPEN_GRAPH, VISIBLE_TEXT), min_auto_confidence=0.6),
    "venue_id": FieldSpec(
        "venue_id", _norm_id, (CANONICAL_ENTITY_RELATIONSHIP, SOURCE_API),
        min_auto_confidence=0.7, derived_allowed=True, scheduler_relevant=True),
    "city": FieldSpec(
        "city", _norm_str, (CANONICAL_ENTITY_RELATIONSHIP, SOURCE_API, JSON_LD, VISIBLE_TEXT),
        min_auto_confidence=0.6, derived_allowed=True, scheduler_relevant=True),
    "region_id": FieldSpec(
        "region_id", _norm_id, (CANONICAL_ENTITY_RELATIONSHIP, SOURCE_API),
        min_auto_confidence=0.7, derived_allowed=True, scheduler_relevant=True),
    "event_status": FieldSpec(
        "event_status", _norm_str, (SOURCE_API, JSON_LD, EMBEDDED_STATE),
        min_auto_confidence=0.6, scheduler_relevant=True),
    "source_on_sale_at": FieldSpec(
        "source_on_sale_at", _norm_dt, (SOURCE_API,), min_auto_confidence=0.8, scheduler_relevant=True),
    "first_ticket_state_seen_at": FieldSpec(
        "first_ticket_state_seen_at", _norm_dt, (TEMPORAL_OBSERVATION,),
        min_auto_confidence=0.5, derived_allowed=True, scheduler_relevant=True),
    "last_observed_not_on_sale_at": FieldSpec(
        "last_observed_not_on_sale_at", _norm_dt, (TEMPORAL_OBSERVATION,),
        min_auto_confidence=0.5, derived_allowed=True),
    "estimated_on_sale_window_start": FieldSpec(
        "estimated_on_sale_window_start", _norm_dt, (TEMPORAL_OBSERVATION,),
        min_auto_confidence=0.5, derived_allowed=True),
    "estimated_on_sale_window_end": FieldSpec(
        "estimated_on_sale_window_end", _norm_dt, (TEMPORAL_OBSERVATION,),
        min_auto_confidence=0.5, derived_allowed=True),
}

SUPPORTED_FIELDS = frozenset(FIELD_REGISTRY)
SCHEDULER_FIELDS = frozenset(f for f, spec in FIELD_REGISTRY.items() if spec.scheduler_relevant)


@dataclass
class Candidate:
    """A pre-persistence field candidate with provenance."""

    field_name: str
    candidate_value: Any
    source_type: str
    extraction_method: str
    confidence: float
    observed_at: datetime
    normalized_value: Any = None
    source_url: str | None = None
    source_published_at: datetime | None = None
    epistemic_status: str = OBSERVED_PUBLIC_STATE

    def normalize(self) -> Candidate:
        spec = FIELD_REGISTRY.get(self.field_name)
        self.normalized_value = spec.normalizer(self.candidate_value) if spec else _norm_str(self.candidate_value)
        return self

    @property
    def surface(self) -> str:
        return surface_meta(self.source_type)[0]

    @property
    def source_family(self) -> str:
        return surface_meta(self.source_type)[1]

    @property
    def independence_group(self) -> str:
        return surface_meta(self.source_type)[2]

    @property
    def content_hash(self) -> str:
        payload = {
            "field": self.field_name, "value": self.normalized_value,
            "source_type": self.source_type, "source_url": self.source_url or "",
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def valid_candidate(c: Candidate) -> bool:
    """A candidate is only valid if its field is supported, the source type is allowed for that
    field, and normalization produced a non-null value (a missing/unparseable value is NOT a
    candidate — never a guessed null)."""
    spec = FIELD_REGISTRY.get(c.field_name)
    if spec is None or c.source_type not in spec.precedence:
        return False
    return c.normalized_value is not None
