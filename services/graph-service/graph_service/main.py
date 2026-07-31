from datetime import UTC, datetime

from fastapi import FastAPI

from graph_service.config import settings
from graph_service.routes.events import router as events_router
from graph_service.routes.graph import router as graph_router

app = FastAPI(
    title="n-quark / graph-service",
    description="Maintains the knowledge graph",
    version="0.1.0",
)

app.include_router(graph_router)
app.include_router(events_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.service_name,
        "graph_backend": settings.graph_backend,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": settings.service_name,
        "message": "Maintains the knowledge graph",
    }
