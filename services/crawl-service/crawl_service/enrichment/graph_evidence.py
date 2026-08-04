"""Build enrichment candidates from the canonical graph (Phase 2.1, highest-confidence path).

The ticketing ingest already projects the event onto the canonical graph: the event node carries
`starts_at`/`city` (from the Boshow structured API fields) and is linked `OCCURS_AT`→venue and
`IN_REGION`→region. City/region/venue are therefore derived via canonical entity relationships,
not asserted as a direct source field.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from crawl_service.enrichment.registry import (
    CANONICAL_ENTITY_RELATIONSHIP,
    DIRECT_FIELD,
    SOURCE_API,
    VENUE_RELATIONSHIP,
    Candidate,
)


def candidates_from_graph(
    node: dict[str, Any] | None, neighbors: list[dict[str, Any]], *, observed_at: datetime,
    origin_source: str = "boshow",
) -> list[Candidate]:
    if not node:
        return []
    props = node.get("properties", {}) or {}
    out: list[Candidate] = []

    def add(field_name, value, source_type, method, conf) -> None:
        if value in (None, ""):
            return
        c = Candidate(
            field_name=field_name, candidate_value=value, source_type=source_type,
            extraction_method=method, confidence=conf, observed_at=observed_at,
            origin_source=origin_source,
        ).normalize()
        if c.normalized_value is not None:
            out.append(c)

    # Direct structured source fields, surfaced via the canonical event node.
    add("starts_at", props.get("starts_at"), SOURCE_API, DIRECT_FIELD, 0.9)
    add("event_status", props.get("event_status"), SOURCE_API, DIRECT_FIELD, 0.85)
    # City derived from the canonical event's location.
    add("city", props.get("city"), CANONICAL_ENTITY_RELATIONSHIP, VENUE_RELATIONSHIP, 0.8)

    for nb in neighbors or []:
        rel = nb.get("relationship")
        n = nb.get("node") or {}
        nid = n.get("id")
        nname = (n.get("properties") or {}).get("display_name")
        if rel == "OCCURS_AT":
            add("venue_id", nid, CANONICAL_ENTITY_RELATIONSHIP, VENUE_RELATIONSHIP, 0.9)
            add("venue_name", nname, SOURCE_API, DIRECT_FIELD, 0.85)
        elif rel == "IN_REGION":
            add("region_id", nid, CANONICAL_ENTITY_RELATIONSHIP, VENUE_RELATIONSHIP, 0.9)
    return out
