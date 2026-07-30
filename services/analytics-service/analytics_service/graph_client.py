from typing import Any

import httpx

from analytics_service.config import settings


class GraphServiceClient:
    """Read-only client for graph-service — the integrated substrate analytics computes over."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.graph_service_url).rstrip("/")

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/v1/graph/nodes/{node_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def neighbors(
        self, node_id: str, *, direction: str = "both", relationship: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {"direction": direction}
        if relationship:
            params["relationship"] = relationship
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/v1/graph/nodes/{node_id}/neighbors", params=params)
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            return resp.json().get("neighbors", [])
