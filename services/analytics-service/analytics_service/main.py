from datetime import UTC, datetime

from fastapi import FastAPI

from analytics_service.config import settings
from analytics_service.routes.analytics import router as analytics_router

app = FastAPI(
    title="n-quark / analytics-service",
    description="Computes deterministic metrics",
    version="0.1.0",
)

app.include_router(analytics_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.service_name,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "message": "Computes deterministic metrics",
    }
