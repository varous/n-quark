import httpx

from signal_service.config import settings
from signal_service.graph_projection import GraphProjection


class GraphServiceClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.graph_service_url).rstrip("/")

    async def upsert_projection(self, projection: GraphProjection) -> dict[str, int]:
        """Upsert a projection (nodes + edges) in one idempotent batch call."""
        if not projection.nodes and not projection.edges:
            return {"nodes": 0, "edges": 0}
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self.base_url}/v1/graph/batch",
                json=projection.to_payload(),
            )
            response.raise_for_status()
            return response.json()
