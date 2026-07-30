"""Project a resolved entity + its observations into knowledge-graph nodes and edges.

This is the pipeline's graph stage. It is **deterministic** (no LLM): given the observations
already produced upstream, it derives a small, explainable set of nodes/edges — the canonical
entity as a node (with identity + popularity as properties) and its structured relationships as
edges (e.g. geographic demand -> ``STRONG_IN`` a region). The projection is idempotent: it
carries no timestamps, so re-ingesting the same signals converges on the same graph.

Only *structured, high-confidence* relationships become edges; softer signals (rising related
queries) ride along as node properties rather than speculative nodes, to keep the graph clean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from signal_service.schemas import NormalizedObservation

# Observation attributes that describe the entity itself -> node properties.
_IDENTITY_PROPS = {
    "musicbrainz_id": "mbid",
    "google_kg_mid": "google_kg_mid",
}
_SIGNAL_PROPS = {
    "subscriber_count",
    "total_view_count",
    "video_count",
    "search_momentum",
}

# Max rising-related queries kept as a node property (avoids unbounded lists).
_MAX_RELATED = 5


@dataclass
class GraphNode:
    id: str
    type: str
    properties: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "properties": self.properties}


@dataclass
class GraphEdge:
    source: str
    relationship: str
    target: str
    properties: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "relationship": self.relationship,
            "target": self.target,
            "properties": self.properties,
        }


@dataclass
class GraphProjection:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_payload() for n in self.nodes],
            "edges": [e.to_payload() for e in self.edges],
        }


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _obs_value(observations: list[NormalizedObservation], attribute: str) -> Any:
    for obs in observations:
        if obs.attribute == attribute:
            return obs.value
    return None


def project_entity_graph(
    *,
    node_id: str,
    entity_type: str,
    display_name: str,
    observations: list[NormalizedObservation],
) -> GraphProjection:
    """Derive graph nodes/edges from a resolved entity and its observations.

    ``node_id`` is the graph key — a canonical entity id when resolution succeeded, otherwise
    the source handle (so the projection still lands, and later unifies once the handle folds
    into the canonical entity as an alias).
    """
    properties: dict[str, Any] = {"display_name": display_name}

    # Identity cross-references + popularity signals -> node properties.
    for attribute, prop in _IDENTITY_PROPS.items():
        value = _obs_value(observations, attribute)
        if value is not None:
            properties[prop] = value
    for attribute in _SIGNAL_PROPS:
        value = _obs_value(observations, attribute)
        if value is not None:
            properties[attribute] = value

    # Classification context (whether the inferred type is trusted).
    for obs in observations:
        if obs.attribute == "candidate_entity_type":
            properties["classification_method"] = obs.evidence.get("method")
            properties["needs_review"] = obs.evidence.get("needs_review", False)
            break

    related = _obs_value(observations, "related_rising_queries")
    if isinstance(related, list) and related:
        properties["rising_related"] = related[:_MAX_RELATED]

    projection = GraphProjection(nodes=[GraphNode(node_id, entity_type, properties)])

    return _with_regions(projection, node_id, observations)


def _with_regions(
    projection: GraphProjection, node_id: str, observations: list[NormalizedObservation]
) -> GraphProjection:
    # Geographic demand -> STRONG_IN edges to region nodes (ranked, deterministic).
    top_regions = _obs_value(observations, "search_top_regions")
    if isinstance(top_regions, list):
        for rank, region in enumerate(top_regions, start=1):
            if not isinstance(region, str) or not region.strip():
                continue
            region_id = f"region:{_slug(region)}"
            projection.nodes.append(GraphNode(region_id, "region", {"display_name": region}))
            projection.edges.append(
                GraphEdge(node_id, "STRONG_IN", region_id, {"rank": rank})
            )

    return projection


def project_ticketing_graph(
    *,
    event_id: str,
    event_properties: dict[str, Any],
    venue_id: str | None,
    venue_name: str | None,
    artists: list[tuple[str, str]],
    region: str | None,
) -> GraphProjection:
    """Project a ticketing event as structural relationships — the first non-demand edges.

    ``artists`` is a list of (canonical_or_handle_id, display_name). Edges:
    event -OCCURS_AT-> venue, event -FEATURES-> artist(s), event -IN_REGION-> region.
    Idempotent (no timestamps); endpoints are created as thin nodes if absent.
    """
    projection = GraphProjection(nodes=[GraphNode(event_id, "event", event_properties)])

    if venue_id:
        projection.nodes.append(
            GraphNode(venue_id, "venue", {"display_name": venue_name} if venue_name else {})
        )
        projection.edges.append(GraphEdge(event_id, "OCCURS_AT", venue_id))

    for artist_id, artist_name in artists:
        if not artist_id:
            continue
        projection.nodes.append(GraphNode(artist_id, "artist", {"display_name": artist_name}))
        projection.edges.append(GraphEdge(event_id, "FEATURES", artist_id))

    if region and region.strip():
        region_id = f"region:{_slug(region)}"
        projection.nodes.append(GraphNode(region_id, "region", {"display_name": region}))
        projection.edges.append(GraphEdge(event_id, "IN_REGION", region_id))

    return projection
