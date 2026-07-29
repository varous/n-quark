from datetime import UTC, datetime

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api_gateway.config import settings

app = FastAPI(
    title="n-quark / api-gateway",
    description="Public API gateway for n-quark intelligence platform",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNSTREAM_SERVICES: dict[str, str] = {
    "crawl": "http://crawl-service:8001",
    "media": "http://media-service:8002",
    "signal": "http://signal-service:8003",
    "observation": "http://observation-service:8004",
    "entity": "http://entity-service:8005",
    "graph": "http://graph-service:8006",
    "analytics": "http://analytics-service:8007",
    "feature": "http://feature-service:8008",
    "intelligence": "http://intelligence-service:8009",
}


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
        "message": "n-quark Intelligence Operating System API",
    }


@app.get("/v1/platform/status")
async def platform_status() -> dict[str, object]:
    """Aggregate health from all downstream services."""
    results: dict[str, object] = {}

    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, base_url in DOWNSTREAM_SERVICES.items():
            try:
                response = await client.get(f"{base_url}/health")
                results[name] = response.json()
            except httpx.HTTPError as exc:
                results[name] = {"status": "error", "detail": str(exc)}

    healthy = all(
        isinstance(v, dict) and v.get("status") == "ok" for v in results.values()
    )

    return {
        "status": "ok" if healthy else "degraded",
        "timestamp": datetime.now(UTC).isoformat(),
        "services": results,
    }
