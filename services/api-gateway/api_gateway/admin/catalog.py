"""Product catalog BFF (Phase 5B.2 increment 2) — first-class Artists & Venues for the market terminal.

Sourced from the AUTHORITATIVE canonical registry (crawl entity-resolution), never from raw graph
artist-type nodes — so source-handle projections (``boshow:artist:…``) can never inflate the product
Artist/Venue counts. Each Artists page is enriched with monitoring state from artist-intelligence in a
single batch call (no per-row round-trips). Degrades gracefully: if artist-intelligence is down the list
still renders identity + live-activity from crawl, with monitoring marked unavailable.
"""

from __future__ import annotations

from typing import Any

from api_gateway.admin.gateway_client import DownstreamGateway

CRAWL = "crawl"
GRAPH = "graph"
DEMAND = "artist_intelligence"
ENTITIES = "/v1/internal/entity-resolution/entities"

# Bounded server-side fan-out for venue aggregation (§5B.2.6 §11): the venue detail read model
# never issues frontend N+1 requests and never traverses the whole graph.
VENUE_EVENT_FANOUT = 60


class CatalogAdminService:
    def __init__(self, gw: DownstreamGateway) -> None:
        self.gw = gw

    async def _count(self, entity_type: str) -> int | None:
        """Total registry-backed canonical count for a product type (Artists/Venues/Organizers).
        Product totals come from crawl's authoritative registry — never raw graph-node counts."""
        r = await self.gw.get(CRAWL, ENTITIES, params={"entity_type": entity_type, "limit": 1, "offset": 0})
        if not r.ok:
            return None
        data = r.data if isinstance(r.data, dict) else {}
        return data.get("count")

    async def product_counts(self) -> dict[str, Any]:
        """Registry-backed product cohort totals for the Overview (§5B.2.6 §5). Suppressed
        (quarantined/review) canonicals are already excluded by the entities endpoint."""
        artists = await self._count("ARTIST")
        venues = await self._count("VENUE")
        organizers = await self._count("ORGANIZER")
        lifecycle = await self.gw.get(CRAWL, "/v1/internal/capture-schedule/lifecycle-diagnostics")
        return {"available": artists is not None or venues is not None,
                "artists": artists, "venues": venues, "organizers": organizers,
                "events": (lifecycle.data or {}) if lifecycle.ok else None}

    async def _canonical(self, entity_type: str, *, limit: int, offset: int,
                         source: str | None = None) -> tuple[list[dict[str, Any]], int | None, bool]:
        params: dict[str, Any] = {"entity_type": entity_type, "limit": limit, "offset": offset}
        if source:
            params["source"] = source
        r = await self.gw.get(CRAWL, ENTITIES, params=params)
        if not r.ok:
            return [], None, False
        data = r.data if isinstance(r.data, dict) else {"entities": r.data}
        rows = data.get("entities") or []
        return rows, data.get("count"), True

    @staticmethod
    def _id(row: dict[str, Any]) -> str | None:
        return row.get("canonical_entity_id") or row.get("canonical_artist_id") or row.get("id")

    async def artists(self, *, limit: int = 50, offset: int = 0, watching: bool = False,
                      youtube_verified: bool = False, needs_identity: bool = False,
                      has_demand: bool = False, moving: bool = False,
                      has_events: bool = False) -> dict[str, Any]:
        rows, count, ok = await self._canonical("ARTIST", limit=limit, offset=offset)
        summ = await self.gw.get(DEMAND, "/v1/internal/artists/summaries")
        summaries: dict[str, Any] = (summ.data or {}).get("summaries", {}) if summ.ok else {}
        items: list[dict[str, Any]] = []
        for row in rows:
            cid = self._id(row)
            if not cid:
                continue
            s = summaries.get(cid, {})
            yt = s.get("youtube_identity_state")
            item = {
                "canonical_artist_id": cid,
                "name": row.get("canonical_name") or row.get("display_name") or cid,
                "events_observed": row.get("linked_event_count", 0),
                "sources": row.get("sources", []),
                "last_observed": row.get("last_observed"),
                "watching": bool(s.get("watching")),
                "watch_status": s.get("watch_status"),
                "youtube_identity_state": yt,                 # RESOLVED|AMBIGUOUS|PENDING|None
                "youtube_verified": yt == "RESOLVED",
                "owned_videos": s.get("owned_videos", 0),
                "has_demand_data": bool(s.get("has_demand_data")),
                "moving_content_count": s.get("moving_content_count", 0),
                "last_demand_update": s.get("last_demand_update"),
            }
            items.append(item)
        # filters (applied on the enriched page)
        if watching:
            items = [i for i in items if i["watching"]]
        if youtube_verified:
            items = [i for i in items if i["youtube_verified"]]
        if needs_identity:
            items = [i for i in items if i["watching"] and not i["youtube_verified"]]
        if has_demand:
            items = [i for i in items if i["has_demand_data"]]
        if moving:
            items = [i for i in items if i["moving_content_count"] > 0]
        if has_events:
            items = [i for i in items if i["events_observed"] > 0]
        return {"available": ok, "monitoring_available": summ.ok, "count": count,
                "limit": limit, "offset": offset, "artists": items}

    async def venues(self, *, limit: int = 50, offset: int = 0,
                     has_events: bool = False) -> dict[str, Any]:
        rows, count, ok = await self._canonical("VENUE", limit=limit, offset=offset)
        items = []
        for row in rows:
            cid = self._id(row)
            if not cid:
                continue
            items.append({
                "canonical_venue_id": cid,
                "name": row.get("canonical_name") or row.get("display_name") or cid,
                "events_observed": row.get("linked_event_count", 0),
                "sources": row.get("sources", []),
                "last_observed": row.get("last_observed"),
            })
        if has_events:
            items = [i for i in items if i["events_observed"] > 0]
        return {"available": ok, "count": count, "limit": limit, "offset": offset, "venues": items}

    async def venue_detail(self, venue_id: str) -> dict[str, Any]:
        """First-class Venue read model (§5B.2.6 §10/§11): activity, Artists who have appeared,
        Organizers active here, Events observed, sources — CANONICAL relationships only (source-handle
        projections are excluded). Bounded server-side fan-out; no frontend N+1, no full-graph pull."""
        ent = await self.gw.get(CRAWL, f"/v1/internal/entity-resolution/entities/VENUE/{venue_id}")
        if not ent.ok:
            return {"available": False, "canonical_venue_id": venue_id}
        data = dict(ent.data or {})
        events: list[str] = list(data.get("linked_events") or [])
        artists: dict[str, str] = {}
        organizers: dict[str, str] = {}
        fanned = events[:VENUE_EVENT_FANOUT]
        for eid in fanned:
            evnode = await self.gw.get(GRAPH, f"/v1/graph/nodes/{eid}")
            nb = await self.gw.get(GRAPH, f"/v1/graph/nodes/{eid}/neighbors", params={"direction": "both"})
            if not nb.ok:
                continue
            for n in (nb.data or {}).get("neighbors", []):
                node = n.get("node") or {}
                nid, ntype = node.get("id"), (node.get("type") or "").lower()
                if not nid:
                    continue
                name = (node.get("properties") or {}).get("display_name") or nid
                if ntype == "artist" and nid.startswith("artist:"):
                    artists.setdefault(nid, name)
                elif ntype == "organizer" and nid.startswith("organizer:"):
                    organizers.setdefault(nid, name)
            props = (evnode.data or {}).get("properties", {}) if evnode.ok else {}
            from api_gateway.admin.event_lifecycle import lifecycle_from_properties
            data.setdefault("event_lifecycle", []).append({"canonical_event_id": eid,
                                                            "lifecycle": lifecycle_from_properties(props)})
        return {
            "available": True,
            "canonical_venue_id": venue_id,
            "name": data.get("canonical_name") or venue_id,
            "city": (data.get("properties") or {}).get("city") or data.get("city"),
            "events_observed": len(events),
            "sources": data.get("sources") or [],
            "last_observed": data.get("last_observed"),
            "events": events,
            "event_lifecycle": data.get("event_lifecycle", []),
            "events_aggregated": len(fanned),
            "events_truncated": len(events) > len(fanned),
            "artists": [{"canonical_artist_id": k, "name": v} for k, v in artists.items()],
            "organizers": [{"canonical_organizer_id": k, "name": v} for k, v in organizers.items()],
            "source_handles": len(data.get("source_handles") or []),
        }
