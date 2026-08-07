import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import FastAPI

from crawl_service.config import settings
from crawl_service.routes.capture_schedule import router as capture_schedule_router
from crawl_service.routes.enrichment import router as enrichment_router
from crawl_service.routes.enrichment_pilot import router as enrichment_pilot_router
from crawl_service.routes.entity_resolution import entities_router as entity_entities_router
from crawl_service.routes.entity_resolution import events_router as entity_events_router
from crawl_service.routes.entity_resolution import router as entity_resolution_router
from crawl_service.routes.governance import router as governance_router
from crawl_service.routes.reconciliation import events_router as reconciliation_events_router
from crawl_service.routes.reconciliation import router as reconciliation_router

@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Phase 4D: run the continuous collector in-process when enabled (Fly always-on collector).
    collector = None
    if settings.collector_enabled and settings.scheduled_capture_enabled:
        from crawl_service.collector import Collector
        from crawl_service.deps import build_scheduler
        collector = Collector(build_scheduler())
        collector.start()
    try:
        yield
    finally:
        if collector is not None:
            await collector.stop()


app = FastAPI(
    title="n-quark / crawl-service",
    description="Collects websites, event pages and metadata",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(capture_schedule_router)
app.include_router(enrichment_router)
app.include_router(enrichment_pilot_router)
app.include_router(reconciliation_router)
app.include_router(reconciliation_events_router)
app.include_router(entity_resolution_router)
app.include_router(entity_entities_router)
app.include_router(entity_events_router)
app.include_router(governance_router)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": settings.service_name,
        "scheduled_capture_enabled": settings.scheduled_capture_enabled,
        "capture_enrichment_enabled": settings.capture_enrichment_enabled,
        "reconciliation_enabled": settings.reconciliation_enabled,
        "entity_resolution_enabled": settings.entity_resolution_enabled,
        "media_observation_enabled": settings.media_observation_enabled,
        "collector_enabled": settings.collector_enabled,
        "collector_sources": sorted(settings.collector_source_set),
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "message": "Collects websites, event pages and metadata",
    }
