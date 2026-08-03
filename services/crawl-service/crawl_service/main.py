from datetime import UTC, datetime

from fastapi import FastAPI

from crawl_service.config import settings
from crawl_service.routes.capture_schedule import router as capture_schedule_router

app = FastAPI(
    title="n-quark / crawl-service",
    description="Collects websites, event pages and metadata",
    version="0.1.0",
)

app.include_router(capture_schedule_router)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": settings.service_name,
        "scheduled_capture_enabled": settings.scheduled_capture_enabled,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "message": "Collects websites, event pages and metadata",
    }
