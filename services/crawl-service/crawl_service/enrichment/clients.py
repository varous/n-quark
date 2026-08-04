"""Async evidence clients for enrichment (Phase 2.1). Injectable so the service is testable offline."""

from __future__ import annotations

import re
from typing import Any, Protocol

import httpx

from crawl_service.config import settings

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class GraphReader(Protocol):
    async def get_event(self, event_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]: ...


class PageFetcher(Protocol):
    async def fetch(self, url: str) -> tuple[str, str] | None: ...  # (html, visible_text) or None


class GraphWriter(Protocol):
    async def upsert_batch(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]: ...


class HttpGraphReader:
    def __init__(self, base_url: str | None = None) -> None:
        self.base = (base_url or settings.graph_service_url).rstrip("/")
        self.timeout = settings.capture_http_timeout_seconds

    async def get_event(self, event_id: str):
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(f"{self.base}/v1/graph/nodes/{event_id}")
            if r.status_code == 404:
                return None, []
            r.raise_for_status()
            node = r.json()
            r2 = await client.get(
                f"{self.base}/v1/graph/nodes/{event_id}/neighbors", params={"direction": "out"}
            )
            neighbors = r2.json().get("neighbors", []) if r2.status_code == 200 else []
            return node, neighbors


class HttpGraphWriter:
    """Writes canonical entity nodes + relationships via graph-service's batch upsert (idempotent)."""

    def __init__(self, base_url: str | None = None) -> None:
        self.base = (base_url or settings.graph_service_url).rstrip("/")
        self.timeout = settings.capture_http_timeout_seconds

    async def upsert_batch(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
        if not nodes and not edges:
            return {"nodes": 0, "edges": 0}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.post(f"{self.base}/v1/graph/batch", json={"nodes": nodes, "edges": edges})
            r.raise_for_status()
            return r.json()


class HttpPageFetcher:
    def __init__(self) -> None:
        self.timeout = settings.capture_http_timeout_seconds

    async def fetch(self, url: str) -> tuple[str, str] | None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                r = await client.get(url, headers={"User-Agent": "n-quark/0.1 (+public enrichment)"})
                r.raise_for_status()
                html = r.text
        except httpx.HTTPError:
            return None
        text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html))
        return html, text
