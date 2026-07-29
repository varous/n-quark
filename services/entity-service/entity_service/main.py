from datetime import UTC, datetime

from fastapi import FastAPI

from entity_service.config import settings

app = FastAPI(
    title="n-quark / entity-service",
    description="Canonicalizes entities",
    version="0.1.0",
)


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
        "message": "Canonicalizes entities",
    }
