from typing import Any

from pydantic import BaseModel, Field


class NodeUpsert(BaseModel):
    id: str = Field(min_length=1, max_length=512)
    type: str = Field(default="unknown", max_length=64)
    properties: dict[str, Any] = Field(default_factory=dict)


class EdgeUpsert(BaseModel):
    source: str = Field(min_length=1, max_length=512)
    relationship: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=512)
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphBatch(BaseModel):
    """A projection: a set of nodes and edges upserted together (idempotent)."""

    nodes: list[NodeUpsert] = Field(default_factory=list)
    edges: list[EdgeUpsert] = Field(default_factory=list)


class BatchResult(BaseModel):
    nodes: int
    edges: int


class NodeRead(BaseModel):
    id: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class EdgeRead(BaseModel):
    source: str
    relationship: str
    target: str
    properties: dict[str, Any] = Field(default_factory=dict)


class NeighborRead(BaseModel):
    relationship: str
    direction: str
    node: NodeRead


class NeighborsResponse(BaseModel):
    node_id: str
    count: int
    neighbors: list[NeighborRead]


class NodeListResponse(BaseModel):
    count: int
    nodes: list[NodeRead]


class GraphStats(BaseModel):
    nodes: int
    edges: int


class EventFeedItem(BaseModel):
    id: str
    name: str | None = None
    category: str | None = None
    city: str | None = None
    region: str | None = None
    region_id: str | None = None
    venue: str | None = None
    venue_id: str | None = None
    organizer: str | None = None
    artists: list[str] = Field(default_factory=list)
    artist_ids: list[str] = Field(default_factory=list)
    starts_at: str | None = None
    price_min: float | None = None
    currency: str | None = None
    is_free: bool = False
    fill_ratio: float | None = None
    image_url: str | None = None
    source: str | None = None
    source_url: str | None = None
    redistribution_tier: str = "open"
    updated_at: str | None = None


class EventFeedResponse(BaseModel):
    count: int
    limit: int
    offset: int
    events: list[EventFeedItem]


# --------------------------------------------------------------------------- Shadow Ledger (internal)
class ShadowObserveRequest(BaseModel):
    """Record one observed public commercial state of an event (Phase 1, internal surface)."""

    source_id: str = Field(min_length=1, max_length=128)
    source_record_id: str | None = Field(default=None, max_length=512)
    observation_id: str | None = Field(default=None, max_length=64)
    observed_at: str | None = Field(default=None, description="ISO 8601; defaults to now")
    # Raw public commercial fields — normalized server-side (deterministic).
    price_min: float | None = None
    currency: str | None = None
    capacity: int | None = None
    tickets_sold: int | None = None
    fill_ratio: float | None = None
    availability: str | None = None
    starts_at: str | None = None
    venue: str | None = None
    status: str | None = None
    # Phase 1.1 capture integrity.
    snapshot_completeness: str | None = Field(
        default=None, description="COMPLETE | PARTIAL (default PARTIAL — conservative)"
    )
    field_status: dict[str, str] = Field(
        default_factory=dict,
        description="per-field: OBSERVED_VALUE|OBSERVED_NULL|NOT_OBSERVED|EXTRACTION_FAILED|NOT_SUPPORTED",
    )
    capture_status: str | None = Field(
        default=None,
        description="CAPTURE_SUCCESS_RECORD_PRESENT|CAPTURE_SUCCESS_RECORD_ABSENT|SOURCE_UNAVAILABLE|"
        "CAPTURE_FAILED|PARSER_FAILED|NOT_CHECKED|EXPLICITLY_REMOVED",
    )
    # Phase 1 presence inputs (still accepted; mapped onto capture_status when it is not given).
    present: bool = True
    absence_reason: str | None = Field(
        default=None,
        description="capture_failure|source_unavailable|parser_failure|record_absent|not_found|explicitly_removed",
    )
    epistemic_status: str = "observed_public_state"
    provenance: dict[str, Any] = Field(default_factory=dict)


class ShadowObserveResponse(BaseModel):
    canonical_event_id: str
    noop: bool
    persisted: bool = True
    out_of_order: bool = False
    capture_status: str | None = None
    absence_count: int = 0
    state: dict[str, Any] | None = None
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    suppressed: list[dict[str, Any]] = Field(default_factory=list)
    trace: dict[str, Any] | None = None


class ShadowLedgerResponse(BaseModel):
    canonical_event_id: str
    source: str | None = None
    detector_version: str
    current_state: dict[str, Any] | None = None
    states: list[dict[str, Any]] = Field(default_factory=list)
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    trace: dict[str, Any] | None = None
