from datetime import UTC, datetime

from fastapi import FastAPI

from signal_service.config import settings
from signal_service.routes.spotify import router as spotify_router

app = FastAPI(
    title="n-quark / signal-service",
    description="Normalizes external API signals into observations",
    version="0.1.0",
)

app.include_router(spotify_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.service_name,
        "spotify_mock": str(settings.use_spotify_mock).lower(),
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "message": "Normalizes external APIs into immutable observations",
    }
