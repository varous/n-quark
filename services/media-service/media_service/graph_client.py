"""Read/write client for graph-service.

Reads the canonical event node (to extract the asset reference the capture wrote) and writes the
`event -USES_CREATIVE-> media_asset` link. It never creates canonical events and never infers
artist/organizer/sponsor relationships from an image.
"""

from typing import Any

import httpx

from media_service.config import settings


class GraphClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.graph_service_url).rstrip("/")

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self.base_url}/v1/graph/nodes/{node_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def upsert_media_asset(self, asset_id: str, properties: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{self.base_url}/v1/graph/nodes",
                                     json={"id": f"media:{asset_id}", "type": "media_asset",
                                           "properties": properties})
            resp.raise_for_status()

    async def link_uses_creative(self, canonical_event_id: str, asset_id: str,
                                 properties: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{self.base_url}/v1/graph/edges",
                                     json={"source": canonical_event_id, "relationship": "USES_CREATIVE",
                                           "target": f"media:{asset_id}", "properties": properties})
            resp.raise_for_status()
