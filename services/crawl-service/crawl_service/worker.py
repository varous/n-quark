"""Scheduled-capture worker (Phase 2).

A run-to-completion command compatible with the existing Fly cron pattern (deploy/fly/ingest-cron):
recover expired locks -> generate due jobs -> claim (lease-locked) -> capture via signal-service ->
record health -> schedule next capture. Idempotent; safe to over-invoke.

Usage:  python -m crawl_service.worker
"""

from __future__ import annotations

import asyncio
import json
import uuid

from crawl_service.capturer import HttpCapturer
from crawl_service.config import settings
from crawl_service.db import SessionLocal, engine
from crawl_service.enrichment.clients import HttpGraphReader, HttpPageFetcher
from crawl_service.enrichment.service import EnrichmentService
from crawl_service.models import Base
from crawl_service.service import SchedulerService


async def _run() -> int:
    if not settings.scheduled_capture_enabled:
        print(json.dumps({"skipped": "scheduled capture disabled"}), flush=True)
        return 0
    Base.metadata.create_all(engine)  # alembic owns prod schema; check-first fallback for dev
    page_fetcher = HttpPageFetcher() if settings.capture_enrichment_public_page_enabled else None
    enricher = EnrichmentService(SessionLocal, HttpGraphReader(), page_fetcher, settings)
    service = SchedulerService(SessionLocal, HttpCapturer(), settings, enricher=enricher)
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    summary = await service.run_once(worker_id, trace=True)
    print(json.dumps(summary, default=str), flush=True)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
