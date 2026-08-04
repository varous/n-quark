"""Downstream access for the admin BFF. Read models call internal service APIs through this; a failing
or disabled downstream degrades gracefully (never a 500) so the console can show partial data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from api_gateway.config import settings


@dataclass
class Down:
    available: bool
    status: int
    data: Any = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.available and 200 <= self.status < 300


class DownstreamGateway:
    """Thin async client over the existing internal service map (crawl/graph/signal/...)."""

    def __init__(self, base_urls: dict[str, str] | None = None, timeout: float = 15.0) -> None:
        self._bases = base_urls or settings.downstream_services
        self._timeout = timeout

    def base(self, service: str) -> str:
        return self._bases[service].rstrip("/")

    async def request(self, service: str, method: str, path: str, *,
                      params: dict | None = None, json: Any = None) -> Down:
        url = f"{self.base(service)}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.request(method, url, params=params, json=json)
        except httpx.HTTPError as exc:
            return Down(available=False, status=0, error=str(exc))
        data: Any = None
        try:
            data = r.json()
        except ValueError:
            data = None
        return Down(available=True, status=r.status_code, data=data)

    async def get(self, service: str, path: str, *, params: dict | None = None) -> Down:
        return await self.request(service, "GET", path, params=params)

    async def post(self, service: str, path: str, *, params: dict | None = None, json: Any = None) -> Down:
        return await self.request(service, "POST", path, params=params, json=json)
