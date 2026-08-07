"""Continuous in-process collector (Phase 4D).

The smallest bounded, restart-safe mechanism to keep a Fly collector both **enrolling new events** and
**capturing tracked ones** — it reuses the existing scheduler (``sync_from_refs`` + ``run_once``); it is
NOT a second scheduling architecture. All durable state lives in Postgres (``tracked_event`` /
``scheduled_capture_job``), so a process restart just resumes idempotently: no lost tracked events, no
duplicated work, no reset Shadow Ledger.

Skillbox is never collected here (``collector_source_set`` strips it), even if listed.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import httpx

from crawl_service.config import settings
from crawl_service.service import SchedulerService


async def discover_refs(source: str, limit: int) -> list[str]:
    """Bounded discovery via signal-service (the same call the manual /sync route makes)."""
    url = f"{settings.signal_service_url}/v1/signals/ticketing/discover"
    async with httpx.AsyncClient(timeout=settings.capture_http_timeout_seconds) as client:
        resp = await client.get(url, params={"limit": limit, "source": source})
        resp.raise_for_status()
        return resp.json().get("event_refs", [])


async def run_cycle(service: SchedulerService, *, sources: list[str], discovery_limit: int,
                    worker_id: str, discover: bool = True, capture: bool = True) -> dict[str, Any]:
    """One collection cycle: (optionally) discover+enroll per source, then run one capture pass.

    Enrollment is idempotent (``sync_from_refs`` upserts tracked events; existing ones aren't
    duplicated), and each source's discovery failure is isolated from the others."""
    result: dict[str, Any] = {"discovery": {}, "capture": None}
    if discover:
        for source in sources:
            try:
                refs = await discover_refs(source, discovery_limit)
                enrolled = service.sync_from_refs(source, refs)
                result["discovery"][source] = {"discovered": len(refs), "enrolled": enrolled}
            except Exception as exc:  # noqa: BLE001 — one source's failure never blocks the others
                result["discovery"][source] = {"error": f"{type(exc).__name__}: {exc}"}
    if capture:
        result["capture"] = await service.run_once(worker_id, trace=False)
    return result


async def collector_loop(service: SchedulerService, *, stop: asyncio.Event) -> None:
    """Run capture every ``capture_interval`` and discovery every ``discovery_interval`` until stopped."""
    sources = sorted(settings.collector_source_set)
    cap_interval = max(30, settings.collector_capture_interval_seconds)
    disc_interval = max(cap_interval, settings.collector_discovery_interval_seconds)
    last_discovery = 0.0
    while not stop.is_set():
        now = time.monotonic()
        do_discover = (now - last_discovery) >= disc_interval
        if do_discover:
            last_discovery = now
        # the loop must survive any single-cycle error and keep running
        with contextlib.suppress(Exception):
            await run_cycle(service, sources=sources, discovery_limit=settings.collector_discovery_limit,
                            worker_id="collector", discover=do_discover, capture=True)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=cap_interval)


class Collector:
    """Owns the background task; started/stopped by the FastAPI lifespan."""

    def __init__(self, service: SchedulerService) -> None:
        self._service = service
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(collector_loop(self._service, stop=self._stop))

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
