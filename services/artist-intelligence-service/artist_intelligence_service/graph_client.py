"""Read-only client for graph-service — the supply side (events an artist is FEATURES-linked to).

Same pattern analytics-service uses (service-to-service reads); demand never mutates the graph or the
event Shadow Ledger. Canonical artist/event identity stays owned by the entity/graph architecture.
"""

from __future__ import annotations

from typing import Any

import httpx

from artist_intelligence_service.config import settings


class GraphServiceClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.graph_service_url).rstrip("/")

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            resp = await client.get(f"{self.base_url}/v1/graph/nodes/{node_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def list_artist_nodes(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Canonical ARTIST graph nodes (the graph REPRESENTATION of canonical artists). Read-only; used
        by 5A.3.2 diagnostics to make registry-vs-graph divergence visible. Never mutates the graph."""
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            resp = await client.get(f"{self.base_url}/v1/graph/nodes",
                                    params={"type": "artist", "limit": limit})
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            body = resp.json()
        return body.get("nodes", body if isinstance(body, list) else [])

    async def neighbors(self, node_id: str, *, direction: str = "both",
                        relationship: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, str] = {"direction": direction}
        if relationship:
            params["relationship"] = relationship
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            resp = await client.get(f"{self.base_url}/v1/graph/nodes/{node_id}/neighbors",
                                    params=params)
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            return resp.json().get("neighbors", [])
