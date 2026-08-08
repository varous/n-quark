"""In-process demand-refresh loop (Phase 5A) — restart-safe, quota-aware, failure-isolated.

Mirrors crawl-service's Phase 4D collector: a bounded background loop that periodically drains the
persisted ``demand_refresh_job`` queue via the scheduler. All state is in Postgres, so a restart just
resumes idempotently. OFF by default; failure of the whole loop never affects the crawl collection spine
(this is a separate service).
"""

from __future__ import annotations

import asyncio
import contextlib

from artist_intelligence_service.config import settings
from artist_intelligence_service.db import SessionLocal
from artist_intelligence_service.scheduler import DemandScheduler


async def _tick(scheduler: DemandScheduler) -> None:
    db = SessionLocal()
    try:
        # Phase 5A.3: keep the universe filling itself — enqueue missing-identity backfill and run
        # bounded YouTube discovery (both flag-gated, quota-aware, persisted) BEFORE draining, so no
        # manual operator invocation is needed. Each is best-effort; a failure never stops the drain.
        if settings.artist_auto_onboard_enabled:
            import contextlib as _c
            from artist_intelligence_service import universe
            with _c.suppress(Exception):
                await universe.backfill_missing_identities(db, scheduler=scheduler)
        if settings.youtube_discovery_enabled:
            import contextlib as _c
            from artist_intelligence_service import discovery
            with _c.suppress(Exception):
                await discovery.run_youtube_discovery(db)
                db.commit()
        await scheduler.run_once(db, worker_id="demand-collector")
    finally:
        db.close()


async def collector_loop(scheduler: DemandScheduler, *, stop: asyncio.Event) -> None:
    interval = max(30, settings.demand_refresh_interval_seconds)
    while not stop.is_set():
        with contextlib.suppress(Exception):   # a single tick's error never stops the loop
            await _tick(scheduler)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)


class DemandCollector:
    def __init__(self, scheduler: DemandScheduler | None = None) -> None:
        self._scheduler = scheduler or DemandScheduler()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(collector_loop(self._scheduler, stop=self._stop))

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
