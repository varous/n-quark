"""One-time collection bootstrap (Phase 4D).

For an empty cloud DB: run one bounded Boshow/District discovery+enrollment and one capture pass through
the existing scheduler, so continuous collection has something tracked to work on. Idempotent — safe to
re-run (``sync_from_refs`` upserts; no duplicate tracked events). Skillbox is excluded. Never enables
Skillbox and never migrates the local dev DB.

Usage:  python -m crawl_service.bootstrap
"""

from __future__ import annotations

import asyncio
import json

from crawl_service.collector import run_cycle
from crawl_service.config import settings
from crawl_service.db import SessionLocal, engine
from crawl_service.deps import build_scheduler
from crawl_service.models import Base


async def _run() -> int:
    if not settings.scheduled_capture_enabled:
        print(json.dumps({"skipped": "scheduled capture disabled"}), flush=True)
        return 0
    Base.metadata.create_all(engine)  # alembic owns prod schema; check-first fallback only
    service = build_scheduler(SessionLocal)
    sources = sorted(settings.collector_source_set)
    result = await run_cycle(service, sources=sources,
                             discovery_limit=settings.collector_discovery_limit,
                             worker_id="bootstrap", discover=True, capture=True)
    print(json.dumps({"bootstrap": result}, default=str), flush=True)
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
