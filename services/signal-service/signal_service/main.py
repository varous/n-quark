from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from signal_service.clients.observation_client import ObservationServiceClient
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


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    """Readiness probe: verifies the observation-service HARD dependency of the capture ingest path.

    The ticketing /ingest write to observation-service is non-optional — if it fails, every capture 502s
    and the collector silently records SOURCE_UNAVAILABLE (nothing is ever captured PRESENT). This surfaces
    that failure explicitly as HTTP 503 with the reason, instead of letting the whole pipeline degrade
    quietly. It is intentionally separate from /health (liveness) so a transient dependency blip does not
    flap the process's health-gated routing."""
    reachable, detail = await ObservationServiceClient().ping()
    body = {
        "service": settings.service_name,
        "status": "ready" if reachable else "not_ready",
        "dependencies": {
            "observation_service": {
                "url": settings.observation_service_url,
                "reachable": reachable,
                "detail": detail,
                "required_by": "ticketing /ingest (capture write path)",
            }
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return JSONResponse(status_code=200 if reachable else 503, content=body)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "message": "Normalizes external APIs into immutable observations",
    }
