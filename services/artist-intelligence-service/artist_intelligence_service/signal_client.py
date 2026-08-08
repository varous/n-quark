"""Acquisition client for signal-service (Phase 5A).

This is the ONLY way the demand layer acquires YouTube data — it reuses signal-service's existing
ingestion path (channel preview) plus the two acquisition-only primitives added for Phase 5A (channel
search, recent videos). The demand service never talks to the YouTube API directly, so there is a
single ingestion path and the API key stays in signal-service.

Returned shapes are plain dicts (provider-agnostic); the YouTube provider maps them to ``DemandDatum``.
"""

from __future__ import annotations

from typing import Any

import httpx

from artist_intelligence_service.config import settings


class SignalAcquisitionError(RuntimeError):
    """A signal-service acquisition call failed (network/5xx/provider error)."""


class SignalClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.signal_service_url).rstrip("/")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
                resp = await client.get(f"{self.base_url}{path}", params=params or {})
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise SignalAcquisitionError(f"signal-service {path} failed: {exc}") from exc

    async def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
                resp = await client.post(f"{self.base_url}{path}", json=json)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            raise SignalAcquisitionError(f"signal-service {path} failed: {exc}") from exc

    async def youtube_videos_batch(self, video_ids: list[str]) -> dict[str, Any]:
        """Batch statistics for known video ids (Phase 5A.3; videos.list, 1 unit / 50 ids)."""
        return await self._post("/v1/signals/youtube/videos/batch", {"video_ids": video_ids})

    async def youtube_search(self, query: str, *, limit: int) -> dict[str, Any]:
        """Bounded channel search for identity discovery. Returns {query, candidates[], mock}."""
        return await self._get("/v1/signals/youtube/search", {"q": query, "limit": limit})

    async def youtube_channel(self, channel_id: str) -> dict[str, Any]:
        """Channel snapshot. Returns YouTubeChannelSignals (observations keyed by attribute)."""
        return await self._get(f"/v1/signals/youtube/channels/{channel_id}/preview")

    async def youtube_verify(self, channel_id: str) -> dict[str, Any]:
        """Authoritative channels.list existence check (Phase 5A.1a). Returns
        {status: FOUND|CHANNEL_NOT_FOUND, ...}. Raises SignalAcquisitionError on provider/network
        failure (HTTP 502) — a transient outage must never look like CHANNEL_NOT_FOUND."""
        return await self._get(f"/v1/signals/youtube/channels/{channel_id}/verify")

    async def youtube_videos(self, channel_id: str, *, limit: int) -> dict[str, Any]:
        """Recent uploaded-video stats by known channel id. Returns YouTubeVideoSignals."""
        return await self._get(f"/v1/signals/youtube/channels/{channel_id}/videos/preview",
                               {"limit": limit})


def channel_stat(signals: dict[str, Any], attribute: str) -> Any:
    """Pull a channel statistic out of a YouTubeChannelSignals payload's observation list."""
    for obs in signals.get("observations") or []:
        if obs.get("attribute") == attribute:
            return obs.get("value")
    return None
