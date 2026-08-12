"""WatchlistAdminService — Phase 5B.1 BFF over artist-intelligence-service's research-watchlist surface.

This is the console's one narrow WRITE capability: RESEARCH CONFIGURATION. The browser talks only to the
gateway; the gateway attaches the authenticated operator identity (``created_by``) to every write and
forwards to the demand service's ``/v1/internal/watchlist`` routes. It never touches canonical entities,
observations, graph nodes, or resolution outcomes — those remain read-only through the rest of the console.

Reads degrade gracefully (``available`` marker) when the demand service is down; writes surface the
downstream status so the caller can report success/failure honestly.
"""

from __future__ import annotations

from typing import Any

from api_gateway.admin.gateway_client import Down, DownstreamGateway

DEMAND = "artist_intelligence"
BASE = "/v1/internal/watchlist"


class WatchlistError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


class WatchlistAdminService:
    def __init__(self, gw: DownstreamGateway) -> None:
        self.gw = gw

    def _unwrap(self, r: Down) -> dict[str, Any]:
        """Return the downstream JSON, or raise WatchlistError with the downstream status/detail so the
        route can map it faithfully (e.g. 404 target-not-found, 422 validation, 503 demand down)."""
        if r.ok:
            return r.data if isinstance(r.data, dict) else {"result": r.data}
        if not r.available:
            raise WatchlistError(503, "watchlist service unavailable")
        detail = "watchlist request failed"
        if isinstance(r.data, dict):
            detail = str(r.data.get("detail") or detail)
        raise WatchlistError(r.status, detail)

    # ---- reads (degrade gracefully) --------------------------------------------------------------
    async def list_targets(self, *, status: str | None = None, limit: int = 100,
                           offset: int = 0) -> dict[str, Any]:
        params = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        r = await self.gw.get(DEMAND, BASE, params=params)
        if not r.ok:
            return {"available": False, "total": 0, "targets": []}
        return {"available": True, **(r.data or {})}

    async def diagnostics(self) -> dict[str, Any]:
        r = await self.gw.get(DEMAND, f"{BASE}/diagnostics")
        if not r.ok:
            return {"available": False}
        return {"available": True, **(r.data or {})}

    async def get_target(self, target_id: str) -> dict[str, Any]:
        return self._unwrap(await self.gw.get(DEMAND, f"{BASE}/{target_id}"))

    # ---- writes (research configuration; created_by is the authenticated operator) --------------
    async def add_target(self, *, created_by: str, display_name: str,
                         canonical_artist_id: str | None = None, youtube_hint: str | None = None,
                         reason: str | None = None, priority: int | None = None,
                         source: str = "OPERATOR") -> dict[str, Any]:
        body = {"created_by": created_by, "display_name": display_name, "source": source,
                "canonical_artist_id": canonical_artist_id, "youtube_hint": youtube_hint,
                "reason": reason, "priority": priority}
        return self._unwrap(await self.gw.post(DEMAND, BASE, json=body))

    async def bulk_preview(self, *, text: str | None = None,
                           names: list[str] | None = None) -> dict[str, Any]:
        return self._unwrap(await self.gw.post(DEMAND, f"{BASE}/bulk/preview",
                                               json={"text": text, "names": names}))

    async def bulk_add(self, *, created_by: str, text: str | None = None,
                       names: list[str] | None = None, reason: str | None = None) -> dict[str, Any]:
        return self._unwrap(await self.gw.post(
            DEMAND, f"{BASE}/bulk",
            json={"created_by": created_by, "text": text, "names": names, "reason": reason}))

    async def pause(self, target_id: str) -> dict[str, Any]:
        return self._unwrap(await self.gw.post(DEMAND, f"{BASE}/{target_id}/pause"))

    async def resume(self, target_id: str) -> dict[str, Any]:
        return self._unwrap(await self.gw.post(DEMAND, f"{BASE}/{target_id}/resume"))

    async def set_priority(self, target_id: str, priority: int) -> dict[str, Any]:
        return self._unwrap(await self.gw.post(DEMAND, f"{BASE}/{target_id}/priority",
                                               json={"priority": priority}))

    async def reject(self, target_id: str, reason: str | None = None) -> dict[str, Any]:
        return self._unwrap(await self.gw.post(DEMAND, f"{BASE}/{target_id}/reject",
                                               json={"reason": reason}))
