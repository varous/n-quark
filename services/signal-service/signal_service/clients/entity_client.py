from typing import Any

import httpx

from signal_service.config import settings


class EntityServiceClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.entity_service_url).rstrip("/")

    async def resolve(
        self,
        *,
        alias: str,
        entity_type: str,
        display_name: str,
        source: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve a source handle to a canonical entity of the classified type."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self.base_url}/v1/entities/resolve",
                json={
                    "alias": alias,
                    "entity_type": entity_type,
                    "display_name": display_name,
                    "source": source,
                    "metadata": metadata or {},
                },
            )
            response.raise_for_status()
            return response.json()

    async def link_aliases(
        self,
        *,
        canonical_id: str,
        aliases: list[str],
        source: str = "identity-crossref",
    ) -> dict[str, Any]:
        """Fold external identity ids (MBID, KG mID) in as aliases on a canonical entity."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self.base_url}/v1/entities/{canonical_id}/aliases",
                json={"aliases": aliases, "source": source},
            )
            response.raise_for_status()
            return response.json()
