from datetime import UTC, datetime

from fastapi import FastAPI

from signal_service.config import settings
from signal_service.routes.google_trends import router as google_trends_router
from signal_service.routes.spotify import router as spotify_router
from signal_service.routes.sources import router as sources_router
from signal_service.routes.ticketing import router as ticketing_router
from signal_service.routes.youtube import router as youtube_router

app = FastAPI(
    title="n-quark / signal-service",
    description="Normalizes external API signals into observations",
    version="0.1.0",
)

app.include_router(spotify_router)
app.include_router(youtube_router)
app.include_router(google_trends_router)
app.include_router(ticketing_router)
app.include_router(sources_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.service_name,
        "spotify_mock": str(settings.use_spotify_mock).lower(),
        "youtube_mock": str(settings.use_youtube_mock).lower(),
        "trends_provider": settings.resolved_trends_provider,
        "ticketing_provider": settings.ticketing_provider,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "message": "Normalizes external APIs into immutable observations",
    }
