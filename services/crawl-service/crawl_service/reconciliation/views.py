"""EventView — a normalized, source-agnostic snapshot of one event for matching (Phase 3).

Built from a canonical graph node + neighbors so both Boshow and District listings are compared on
the same shape without touching source-specific truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)).replace(tzinfo=None)  # compare wall clock
    except (ValueError, TypeError):
        return None


@dataclass
class EventView:
    source: str
    source_record_id: str
    canonical_event_id: str | None
    title: str = ""
    starts_at: datetime | None = None
    city: str | None = None
    region_id: str | None = None
    venue_name: str | None = None
    venue_id: str | None = None
    organizer: str | None = None
    performers: list[str] = field(default_factory=list)
    price_min: float | None = None
    currency: str | None = None
    availability: str | None = None

    @classmethod
    def from_graph(cls, *, source: str, source_record_id: str, node: dict[str, Any] | None,
                   neighbors: list[dict[str, Any]]) -> EventView:
        props = (node or {}).get("properties", {}) if node else {}
        venue_name = venue_id = region_id = None
        performers: list[str] = []
        for nb in neighbors or []:
            rel, n = nb.get("relationship"), nb.get("node") or {}
            nm = (n.get("properties") or {}).get("display_name")
            if rel == "OCCURS_AT":
                venue_id, venue_name = n.get("id"), nm
            elif rel == "IN_REGION":
                region_id = n.get("id")
            elif rel == "FEATURES" and nm:
                performers.append(nm)
        return cls(
            source=source, source_record_id=source_record_id,
            canonical_event_id=(node or {}).get("id"),
            title=props.get("display_name") or "", starts_at=_dt(props.get("starts_at")),
            city=props.get("city"), region_id=region_id, venue_name=venue_name, venue_id=venue_id,
            organizer=props.get("organizer"), performers=performers,
            price_min=props.get("price_min"), currency=props.get("currency"),
            availability=props.get("availability"),
        )
