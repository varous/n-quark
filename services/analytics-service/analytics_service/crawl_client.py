from typing import Any

import httpx

from analytics_service.config import settings


class CrawlServiceClient:
    """Read-only client for crawl-service — the capture/entity-resolution owner analytics reads from."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.crawl_service_url).rstrip("/")

    async def _get(self, client: httpx.AsyncClient, path: str, **params: Any) -> Any:
        resp = await client.get(f"{self.base_url}{path}", params={k: v for k, v in params.items() if v is not None})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def capture_schedule(self, client: httpx.AsyncClient, *, limit: int = 500) -> list[dict]:
        data = await self._get(client, "/v1/internal/capture-schedule", limit=limit)
        return (data or {}).get("events", [])

    async def entities(self, client: httpx.AsyncClient, *, limit: int = 200) -> list[dict]:
        data = await self._get(client, "/v1/internal/entity-resolution/entities", limit=limit)
        return (data or {}).get("entities", [])

    async def governance_counts(self, client: httpx.AsyncClient) -> dict:
        return await self._get(client, "/v1/internal/governance/counts") or {}

    async def resolved_entities(self, client: httpx.AsyncClient, event_id: str) -> list[dict]:
        data = await self._get(client, f"/v1/internal/events/{event_id}/resolved-entities")
        return (data or {}).get("entities", [])
