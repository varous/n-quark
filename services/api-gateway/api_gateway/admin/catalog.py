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
DEMAND = "artist_intelligence"
ENTITIES = "/v1/internal/entity-resolution/entities"


class CatalogAdminService:
    def __init__(self, gw: DownstreamGateway) -> None:
        self.gw = gw

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
