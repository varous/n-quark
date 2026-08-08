"""DemandAdminService — Phase 5A.2 read-only BFF over artist-intelligence-service (the demand layer).

Reshapes the demand service's internal read models into bounded frontend presentation contracts. The
browser talks only to this gateway surface; it never calls artist-intelligence-service directly. Every
method degrades gracefully when the demand service is unavailable/disabled (returns partial data with an
``available`` marker) so a demand-layer outage never breaks the rest of the inspection console.

STRICTLY read-only: no resolve/refresh/import/scheduler-mutation is exposed. Analytics stay in the
demand service — this only fetches, bounds, and normalises.
"""

from __future__ import annotations

from typing import Any

from api_gateway.admin.gateway_client import DownstreamGateway

DEMAND = "artist_intelligence"   # downstream key (see config.downstream_services)
CRAWL = "crawl"

# Bounds so an event with many resolved artists never fans out unboundedly into the demand service.
MAX_EVENT_ARTISTS = 6
MAX_OBSERVATIONS = 200


class DemandAdminService:
    def __init__(self, gateway: DownstreamGateway | None = None) -> None:
        self.gw = gateway or DownstreamGateway()

    # ---- operations / diagnostics ---------------------------------------------------------------
    async def overview(self) -> dict[str, Any]:
        """Coverage + YouTube provider health (REAL/MOCK) + quota + scheduler + Trends status."""
        cov = await self.gw.get(DEMAND, "/v1/internal/demand/coverage")
        health = await self.gw.get(DEMAND, "/v1/internal/demand/provider-health")
        quota = await self.gw.get(DEMAND, "/v1/internal/demand/quota")
        sched = await self.gw.get(DEMAND, "/v1/internal/demand/scheduler")
        # "available" means the demand service answered at least one call — the console degrades the
        # whole panel (not the app) when this is false.
        available = any(r.available for r in (cov, health, quota, sched))
        return {
            "available": available,
            "coverage": cov.data if cov.ok else None,
            "provider_health": health.data if health.ok else None,
            "quota": quota.data if quota.ok else None,
            "scheduler": sched.data if sched.ok else None,
            "downstream": {"coverage": cov.ok, "provider_health": health.ok,
                           "quota": quota.ok, "scheduler": sched.ok},
        }

    async def summary(self) -> dict[str, Any]:
        """Compact demand summary for the main dashboard (a few coverage + health headlines)."""
        cov = await self.gw.get(DEMAND, "/v1/internal/demand/coverage")
        health = await self.gw.get(DEMAND, "/v1/internal/demand/provider-health")
        sched = await self.gw.get(DEMAND, "/v1/internal/demand/scheduler")
        c = cov.data or {} if cov.ok else {}
        yt_status = (c.get("youtube_identity_status") or {})
        yh = ((health.data or {}).get("providers", {}).get("youtube", {}) if health.ok else {})
        s = sched.data or {} if sched.ok else {}
        return {
            "available": any(r.available for r in (cov, health, sched)),
            "resolved_youtube_artists": yt_status.get("resolved"),
            "artists_with_youtube_identity": yt_status.get("artists_with_youtube_identity"),
            "artists_with_demand_observation": c.get("artists_with_demand_observation"),
            "stale_demand_artists": c.get("stale_demand_artists"),
            "youtube_mode": (yh.get("mode") or {}).get("mode") if isinstance(yh.get("mode"), dict) else yh.get("mode"),
            "scheduler_enabled": s.get("enabled"),
            "scheduler_terminal_failures": s.get("terminal_failures"),
        }

    async def provider_health(self) -> dict[str, Any]:
        r = await self.gw.get(DEMAND, "/v1/internal/demand/provider-health")
        return {"available": r.available, **(r.data or {})} if r.data else {"available": r.available}

    async def quota(self) -> dict[str, Any]:
        r = await self.gw.get(DEMAND, "/v1/internal/demand/quota")
        return {"available": r.available, **(r.data or {})} if r.data else {"available": r.available}

    async def scheduler(self) -> dict[str, Any]:
        r = await self.gw.get(DEMAND, "/v1/internal/demand/scheduler")
        return {"available": r.available, **(r.data or {})} if r.data else {"available": r.available}

    # ---- artist demand --------------------------------------------------------------------------
    async def artist(self, artist_id: str) -> dict[str, Any]:
        """Full per-artist demand bundle: identities + YouTube + Trends + supply + momentum + geography."""
        demand = await self.gw.get(DEMAND, f"/v1/internal/artists/{artist_id}/demand")
        momentum = await self.gw.get(DEMAND, f"/v1/internal/artists/{artist_id}/momentum")
        geography = await self.gw.get(DEMAND, f"/v1/internal/artists/{artist_id}/geography")
        d = demand.data or {} if demand.ok else {}
        return {
            "canonical_artist_id": artist_id,
            "available": demand.available,
            "external_identities": d.get("external_identities", []),
            "youtube": d.get("youtube"),
            "google_trends": d.get("google_trends"),
            "observed_live_supply": d.get("observed_live_supply"),
            "momentum": momentum.data if momentum.ok else None,
            "geography": geography.data if geography.ok else None,
            "notes": d.get("notes", []),
            "downstream": {"demand": demand.ok, "momentum": momentum.ok, "geography": geography.ok},
        }

    async def observations(self, artist_id: str, *, provider: str | None = None,
                           metric: str | None = None, limit: int = 100,
                           offset: int = 0) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": min(limit, MAX_OBSERVATIONS), "offset": offset}
        if provider:
            params["provider"] = provider
        if metric:
            params["metric"] = metric
        r = await self.gw.get(DEMAND, f"/v1/internal/artists/{artist_id}/observations", params=params)
        if not r.ok:
            return {"canonical_artist_id": artist_id, "available": r.available,
                    "total": 0, "items": []}
        return {"available": True, **(r.data or {})}

    # ---- event demand context -------------------------------------------------------------------
    async def event_context(self, event_id: str) -> dict[str, Any]:
        """Per-resolved-artist demand context for an event: momentum components + event-response
        co-movement timeline. Bounded to the first few resolved ARTIST entities."""
        resolved = await self.gw.get(CRAWL, f"/v1/internal/events/{event_id}/resolved-entities")
        entities = (resolved.data or {}).get("entities", []) if resolved.ok else []
        artist_ids: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        for e in entities:
            if str(e.get("entity_type") or "").upper() != "ARTIST":
                continue
            cid = e.get("canonical_entity_id")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            artist_ids.append((cid, e.get("raw_name")))
            if len(artist_ids) >= MAX_EVENT_ARTISTS:
                break

        artists: list[dict[str, Any]] = []
        for cid, name in artist_ids:
            demand = await self.gw.get(DEMAND, f"/v1/internal/artists/{cid}/demand")
            momentum = await self.gw.get(DEMAND, f"/v1/internal/artists/{cid}/momentum")
            resp = await self.gw.get(DEMAND, f"/v1/internal/artists/{cid}/event-response",
                                     params={"event_id": event_id})
            d = demand.data or {} if demand.ok else {}
            artists.append({
                "canonical_artist_id": cid, "raw_name": name,
                "available": demand.available,
                "youtube": d.get("youtube"),
                "google_trends": d.get("google_trends"),
                "momentum": momentum.data if momentum.ok else None,
                "event_response": resp.data if resp.ok else None,
            })
        return {
            "canonical_event_id": event_id,
            "available": resolved.available,
            "resolved_artist_count": len(artist_ids),
            "capped": len(artist_ids) >= MAX_EVENT_ARTISTS,
            "artists": artists,
            "notes": ["temporal co-movement only; no causal inference between demand and event state"],
        }
