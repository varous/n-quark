from datetime import UTC, datetime

from fastapi import FastAPI

from media_service.config import settings
from media_service.routes.media import router as media_router

app = FastAPI(
    title="n-quark / media-service",
    description="Observes public event creatives over time (content-addressed, deterministic).",
    version="0.2.0",
)

app.include_router(media_router)


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "service": settings.service_name,
        "media_observation_enabled": settings.media_observation_enabled,
        "media_fetch_enabled": settings.media_fetch_enabled,
        "media_storage_enabled": settings.media_storage_enabled,
        "media_graph_link_enabled": settings.media_graph_link_enabled,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "message": "Observes public event creatives over time",
    }
