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
            resp = await client.get(f"{self.base_url}/v1/internal/entity-resolution/entities",
                                    params={"entity_type": "ARTIST", "limit": limit, "offset": offset})
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            body = resp.json()
        rows = body.get("entities") if isinstance(body, dict) else body
        return list(rows or [])

    async def find_artist_by_name(self, name: str) -> dict[str, Any] | None:
        """Deterministic name match against existing canonical ARTIST entities (owned by crawl/graph).

        Used by candidate promotion to LINK to an existing canonical artist rather than create a new one.
        Bounded scan of the entities listing; comparison is on the normalized name only (no fuzzy guess)."""
        import re

        def norm(s: str) -> str:
            return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

        target = norm(name)
        if not target:
            return None
        offset = 0
        for _ in range(10):  # bounded pages
            rows = await self.artists(limit=200, offset=offset)
            if not rows:
                break
            for r in rows:
                cn = r.get("canonical_name") or r.get("display_name") or ""
                cid = r.get("canonical_entity_id") or r.get("canonical_artist_id") or r.get("id")
                if cid and norm(str(cn)) == target:
                    return {"canonical_entity_id": cid, "canonical_name": cn}
            if len(rows) < 200:
                break
            offset += 200
        return None

    async def create_artist(self, canonical_name: str, *, provenance: dict[str, Any] | None = None,
                            source: str | None = None) -> dict[str, Any]:
        """Create/match a canonical ARTIST through the crawl-owned governance path (5A.3.1). The demand
        service NEVER writes canonical identity to its own DB — ownership stays in crawl/graph."""
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            resp = await client.post(
                f"{self.base_url}/v1/internal/governance/create-artist",
                json={"canonical_name": canonical_name, "provenance": provenance or {}, "source": source})
            resp.raise_for_status()
            return resp.json()
