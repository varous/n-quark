"""Read-only client for crawl-service — enumerates canonical artists (cohort + coverage).

Canonical artists are owned by the entity/graph architecture and surfaced by crawl-service's internal
entities endpoint (the same source analytics-service reads). This service never creates them.
"""

from __future__ import annotations

from typing import Any

import httpx

from artist_intelligence_service.config import settings


class CrawlServiceClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.crawl_service_url).rstrip("/")

    async def artists(self, *, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            resp = await client.get(f"{self.base_url}/v1/internal/entities",
                                    params={"entity_type": "ARTIST", "limit": limit, "offset": offset})
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            body = resp.json()
        rows = body.get("entities") if isinstance(body, dict) else body
        return list(rows or [])
