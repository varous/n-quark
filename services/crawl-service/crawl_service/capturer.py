"""Capture boundary (Phase 2).

The scheduler NEVER touches the Shadow Ledger detector directly. It captures through production
service boundaries:
  - present captures go through signal-service's existing ingest route, which itself submits the
    structured commercial-state capture to the Shadow Ledger;
  - authoritative absence (source reachable, record genuinely gone -> HTTP 404) is submitted to
    graph-service's Shadow Ledger *observe* endpoint with capture_status so disappearance evidence
    accrues.
A failed request (timeout / 5xx / rate-limit) is NEVER reported as absence.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from crawl_service.classification import (
    INVALID_RESPONSE,
    PARSER_FAILED,
    RATE_LIMITED,
    SOURCE_UNAVAILABLE,
    SUCCESS_RECORD_ABSENT,
    SUCCESS_RECORD_PRESENT,
    TIMEOUT,
    CaptureOutcome,
)
from crawl_service.config import settings


class Capturer(Protocol):
    async def capture(
        self, *, source: str, source_record_id: str, canonical_event_id: str | None
    ) -> CaptureOutcome: ...


def _retry_after(resp: httpx.Response) -> int | None:
    raw = resp.headers.get("Retry-After")
    if raw and raw.isdigit():
        return int(raw)
    return None


class HttpCapturer:
    """Talks to signal-service (ingest) and graph-service (absence observe) over HTTP."""

    def __init__(self, signal_url: str | None = None, graph_url: str | None = None) -> None:
        self.signal_url = (signal_url or settings.signal_service_url).rstrip("/")
        self.graph_url = (graph_url or settings.graph_service_url).rstrip("/")
        self.timeout = settings.capture_http_timeout_seconds

    async def capture(self, *, source, source_record_id, canonical_event_id) -> CaptureOutcome:
        url = f"{self.signal_url}/v1/signals/ticketing/events/{source_record_id}/ingest"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, params={"source": source})  # per-source provider (Phase 3)
        except httpx.TimeoutException as exc:
            return CaptureOutcome(TIMEOUT, error=str(exc))
        except httpx.HTTPError as exc:
            return CaptureOutcome(SOURCE_UNAVAILABLE, error=str(exc))

        if resp.status_code == 200:
            body = resp.json()
            resolved = (body.get("resolved") or {}).get("event")
            return CaptureOutcome(
                SUCCESS_RECORD_PRESENT, http_status=200,
                shadow_result=body.get("shadow_ledger"),
                canonical_event_id=resolved or canonical_event_id,
                detail={"starts_at": body.get("starts_at"), "city": body.get("city")},
            )
        if resp.status_code == 404:
            # Source reachable, record genuinely absent -> authoritative absence.
            await self._submit_absence(source, source_record_id, canonical_event_id)
            return CaptureOutcome(SUCCESS_RECORD_ABSENT, http_status=404,
                                  canonical_event_id=canonical_event_id)
        if resp.status_code == 429:
            return CaptureOutcome(RATE_LIMITED, http_status=429, retry_after_seconds=_retry_after(resp))
        if resp.status_code == 422:
            return CaptureOutcome(INVALID_RESPONSE, http_status=422)
        if resp.status_code in (500, 502, 503, 504):
            # signal-service maps adapter/source failures to 502 — a failed request, NOT absence.
            return CaptureOutcome(SOURCE_UNAVAILABLE, http_status=resp.status_code)
        return CaptureOutcome(PARSER_FAILED, http_status=resp.status_code)

    async def _submit_absence(self, source, source_record_id, canonical_event_id) -> None:
        if not canonical_event_id:
            return  # never captured present -> no canonical id to attach absence to
        url = f"{self.graph_url}/v1/internal/events/{canonical_event_id}/shadow-ledger/observe"
        payload = {
            "source_id": source,
            "source_record_id": source_record_id,
            "capture_status": "CAPTURE_SUCCESS_RECORD_ABSENT",
            "present": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await client.post(url, json=payload)
        except httpx.HTTPError:
            return  # best-effort; the next run will retry the absence
