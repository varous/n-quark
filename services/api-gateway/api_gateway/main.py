from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api_gateway.config import settings
from api_gateway.routes.admin import router as admin_router
from api_gateway.routes.admin_commands import router as admin_commands_router
from api_gateway.routes.proxy import router as proxy_router

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

app.include_router(proxy_router)
# Admin BFF: routes self-gate on ADMIN_API_ENABLED via the auth dependency (404 when disabled).
app.include_router(admin_router)
app.include_router(admin_commands_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.service_name,
        "network_mode": settings.network_mode,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "message": "n-quark Intelligence Operating System API",
    }


def _mount_frontend() -> None:
    """Single-app deployment (Admin D): serve the built SPA at / when a bundle is present.

    Mounted LAST so every explicit API route (/admin/v1, /v1/..., /health) is matched first; the static
    app only handles /, /assets/* and other bundle files. The SPA uses hash routing, so deep links land
    on / and route client-side. No-op when the bundle isn't shipped (e.g. the private services)."""
    directory = settings.admin_frontend_dir
    if not directory:
        return
    index = Path(directory) / "index.html"
    if not index.is_file():
        return
    app.mount("/", StaticFiles(directory=directory, html=True), name="frontend")


@app.get("/v1/platform/status")
async def platform_status() -> dict[str, object]:
    """Aggregate health from all downstream services."""
    results: dict[str, object] = {}
    downstream = settings.downstream_services

    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, base_url in downstream.items():
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
        "network_mode": settings.network_mode,
        "timestamp": datetime.now(UTC).isoformat(),
        "services": results,
    }


# Mount the SPA last, after every API route is registered.
_mount_frontend()
