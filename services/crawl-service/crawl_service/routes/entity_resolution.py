"""Internal cross-inventory entity-resolution endpoints (Phase 3.1). Internal only; flag-gated."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from crawl_service.config import settings
from crawl_service.deps import get_entity_resolution_service
from crawl_service.entity_resolution.service import EntityResolutionService

router = APIRouter(prefix="/v1/internal/entity-resolution", tags=["entity-resolution (internal)"])
entities_router = APIRouter(prefix="/v1/internal/entities", tags=["entity-resolution (internal)"])
events_router = APIRouter(prefix="/v1/internal/events", tags=["entity-resolution (internal)"])


@router.post("/run", summary="Resolve entities across captured source events (internal)")
async def run(
    sources: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=2000),
    trace: bool = Query(default=False),
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    if not settings.entity_resolution_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="entity resolution disabled")
    src = [s.strip() for s in sources.split(",") if s.strip()] if sources else None
    return await svc.run(sources=src, limit=limit, trace=trace)


@router.get("/coverage", summary="Cross-source entity coverage metrics (internal)")
def coverage(
    source: str | None = Query(default=None),
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    return svc.coverage(source=source)


@router.get("/cross-inventory", summary="Entities shared across sources — convergence proof (internal)")
def cross_inventory(
    entity_type: str = Query(default="ARTIST"),
    limit: int = Query(default=50, ge=1, le=500),
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    return svc.cross_inventory(entity_type=entity_type, limit=limit)


@router.get("/unresolved", summary="Unresolved / ambiguous / possible entity queue (internal)")
def unresolved(
    entity_type: str | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    return svc.unresolved(entity_type=entity_type, source=source, limit=limit)


@router.get("/candidates/{candidate_id}", summary="One resolution candidate + its history (internal)")
def candidate(
    candidate_id: str,
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    data = svc.candidate(candidate_id)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="candidate not found")
    return data


@entities_router.get("/{entity_type}/{entity_id}/source-handles",
                     summary="Source handles that identify a canonical entity (internal)")
def source_handles(
    entity_type: str, entity_id: str,
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    return svc.source_handles(entity_type, entity_id)


@events_router.get("/{event_id}/resolved-entities",
                   summary="Canonical entities resolved for one source event (internal)")
def resolved_entities(
    event_id: str,
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    return svc.resolved_entities(event_id)
