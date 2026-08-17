"""SocialAdminService — Phase 5C.1 read-only BFF over crawl-service's social evidence spine.

Surfaces social collection coverage/diagnostics: identities attached to canonical entities, durable
SocialMention evidence (claims + provenance, never raw content), watchlist eligibility and honest
access state. STRICTLY read-only and never a raw-source bulk export — the browser sees derived
coverage, not a downloadable post catalogue. Degrades gracefully when crawl is unavailable/disabled.
"""

from __future__ import annotations

from typing import Any

from api_gateway.admin.gateway_client import DownstreamGateway

CRAWL = "crawl"


class SocialAdminService:
    def __init__(self, gateway: DownstreamGateway | None = None) -> None:
        self.gw = gateway or DownstreamGateway()

    async def overview(self) -> dict[str, Any]:
        cov = await self.gw.get(CRAWL, "/v1/internal/social/coverage")
        watch = await self.gw.get(CRAWL, "/v1/internal/social/watchlist", params={"limit": 50})
        return {
            "available": cov.available,
            "coverage": cov.data if cov.ok else None,
            "watchlist": watch.data if watch.ok else None,
        }

    async def identities(self, *, canonical_entity_id: str | None = None,
                         platform: str | None = None, limit: int = 200) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if canonical_entity_id:
            params["canonical_entity_id"] = canonical_entity_id
        if platform:
            params["platform"] = platform
        r = await self.gw.get(CRAWL, "/v1/internal/social/identities", params=params)
        return r.data if r.ok else {"available": False, "count": 0, "items": []}

    async def mentions(self, *, canonical_entity_id: str | None = None, platform: str | None = None,
                       limit: int = 100) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if canonical_entity_id:
            params["canonical_entity_id"] = canonical_entity_id
        if platform:
            params["platform"] = platform
        r = await self.gw.get(CRAWL, "/v1/internal/social/mentions", params=params)
        return r.data if r.ok else {"available": False, "count": 0, "items": []}
