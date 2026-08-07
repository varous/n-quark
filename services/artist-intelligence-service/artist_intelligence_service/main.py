from contextlib import asynccontextmanager

from fastapi import FastAPI

from artist_intelligence_service.collector import DemandCollector
from artist_intelligence_service.config import settings
from artist_intelligence_service.routes.demand import router as demand_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    collector: DemandCollector | None = None
    if settings.demand_intelligence_enabled and settings.demand_scheduler_enabled:
        collector = DemandCollector()
        collector.start()
    try:
        yield
    finally:
        if collector is not None:
            await collector.stop()


app = FastAPI(title="artist-intelligence-service", version="0.1.0", lifespan=lifespan)
app.include_router(demand_router)


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": settings.service_name,
        "demand_intelligence_enabled": settings.demand_intelligence_enabled,
        "youtube_enabled": settings.youtube_enabled,
        "google_trends_mode": settings.resolved_trends_mode,
        "demand_scheduler_enabled": settings.demand_scheduler_enabled,
    }
