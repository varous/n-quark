"""Observed live SUPPLY for a canonical artist (Phase 5A) — read from the graph, joined to DEMAND
through ``canonical_artist_id`` only. No combined score is computed; supply and demand are juxtaposed.

Supply = events an artist is FEATURES-linked to in the graph (event -FEATURES-> artist), plus each
event's region/venue/organizer/city. Region is keyed by a normalized name slug so it can align with
the Trends geography (which is ISO-coded with the same human label).
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any

from artist_intelligence_service.graph_client import GraphServiceClient

_DAYS_90 = timedelta(days=90)


def region_slug(value: str) -> str:
    """Normalize a region id/label to a comparable key so the demand geography (keyed by the region's
    human label, e.g. 'West Bengal') aligns with graph region nodes (e.g. 'region:west-bengal')."""
    v = re.sub(r"^region:", "", (value or "").strip(), flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "-", v.lower()).strip("-") or "unknown"


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        from datetime import UTC
        dt = dt.replace(tzinfo=UTC)
    return dt


def _first(props: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if props.get(k) not in (None, ""):
            return props.get(k)
    return None


async def artist_supply(graph: GraphServiceClient, artist_id: str, *, now: datetime,
                        max_events: int = 200) -> dict[str, Any]:
    """Deterministic observed-supply summary for one artist. Region-keyed for the geography join."""
    features = await graph.neighbors(artist_id, direction="in", relationship="FEATURES")
    events = [n for n in features if (n.get("node") or {}).get("id")][:max_events]

    sem = asyncio.Semaphore(8)

    async def hydrate(evt: dict[str, Any]) -> dict[str, Any]:
        node = evt.get("node") or {}
        eid = node.get("id")
        props = node.get("properties") or {}
        async with sem:
            out_neigh = await graph.neighbors(eid, direction="out")
        region_id = venue = organizer = None
        for nb in out_neigh:
            nid = (nb.get("node") or {}).get("id") or ""
            if nid.startswith("region:") or nb.get("relationship") == "IN_REGION":
                region_id = region_id or nid
            elif nid.startswith("venue:"):
                venue = venue or nid
            elif nid.startswith("organizer:"):
                organizer = organizer or nid
        return {"event_id": eid, "title": _first(props, "title", "name"),
                "city": _first(props, "city"), "starts_at": _first(props, "starts_at", "start_time"),
                "status": _first(props, "status", "event_status"),
                "region_id": region_id, "venue_id": venue, "organizer_id": organizer}

    hydrated = await asyncio.gather(*(hydrate(e) for e in events)) if events else []

    upcoming, recent = 0, 0
    cities, venues, organizers = set(), set(), set()
    regions: dict[str, dict[str, Any]] = {}
    first_seen = last_seen = None
    for e in hydrated:
        st = _parse_dt(e["starts_at"])
        if st:
            first_seen = st if first_seen is None else min(first_seen, st)
            last_seen = st if last_seen is None else max(last_seen, st)
            if st >= now:
                upcoming += 1
            elif st >= now.replace(microsecond=0) - _DAYS_90:
                recent += 1
        if e["city"]:
            cities.add(e["city"])
        if e["venue_id"]:
            venues.add(e["venue_id"])
        if e["organizer_id"]:
            organizers.add(e["organizer_id"])
        if e["region_id"]:
            slug = region_slug(e["region_id"])
            r = regions.setdefault(slug, {"region_slug": slug, "region_id": e["region_id"],
                                          "event_count": 0, "upcoming": 0, "recent": 0})
            r["event_count"] += 1
            if st and st >= now:
                r["upcoming"] += 1
            elif st and st >= now - _DAYS_90:
                r["recent"] += 1

    return {
        "artist_id": artist_id,
        "event_count": len(hydrated),
        "upcoming_events": upcoming,
        "recent_events": recent,
        "cities": sorted(cities),
        "regions": regions,
        "venues": sorted(venues),
        "organizers": sorted(organizers),
        "first_observed": first_seen.isoformat() if first_seen else None,
        "last_observed": last_seen.isoformat() if last_seen else None,
        "events": hydrated,
    }
