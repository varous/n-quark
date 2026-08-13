"""Internal cross-inventory entity-resolution endpoints (Phase 3.1). Internal only; flag-gated."""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

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


@router.post("/resolve", summary="Re-run entity resolution for one event (internal)")
async def resolve_one(
    event_id: str = Query(...),
    source: str = Query(...),
    source_record_id: str = Query(...),
    trace: bool = Query(default=False),
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    if not settings.entity_resolution_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="entity resolution disabled")
    return await svc.resolve_event(canonical_event_id=event_id, source=source,
                                   source_record_id=source_record_id, trace=trace)


@router.get("/coverage", summary="Cross-source entity coverage metrics (internal)")
def coverage(
    source: str | None = Query(default=None),
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    return svc.coverage(source=source)


@router.get("/quality-audit", summary="Canonical data-quality audit + dry-run manifest (internal, 5B.2.3)")
def quality_audit(
    limit: int = Query(default=100, ge=1, le=500),
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    return svc.quality_audit(limit=limit)


@router.get("/quality-metrics", summary="Live integrity-flow metrics (internal, 5B.2.5)")
def quality_metrics(svc: EntityResolutionService = Depends(get_entity_resolution_service)) -> dict[str, Any]:
    return svc.quality_metrics()


@router.get("/review-queue", summary="Live interpretation review items (internal, 5B.2.4)")
def review_queue(
    limit: int = Query(default=100, ge=1, le=500),
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    return svc.review_queue(limit=limit)


@router.post("/correct", summary="Governed data-quality correction (internal, 5B.2.4)")
def correct(payload: dict = Body(...),
            svc: EntityResolutionService = Depends(get_entity_resolution_service)) -> dict[str, Any]:
    try:
        return svc.apply_correction(
            action=str(payload.get("action", "")), actor=str(payload.get("actor", "")),
            reason=payload.get("reason"), canonical_entity_id=payload.get("canonical_entity_id"),
            candidate_id=payload.get("candidate_id"))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/cross-inventory", summary="Entities shared across sources — convergence proof (internal)")
def cross_inventory(
    entity_type: str = Query(default="ARTIST"),
    limit: int = Query(default=50, ge=1, le=500),
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    return svc.cross_inventory(entity_type=entity_type, limit=limit)


@router.get("/entities", summary="List canonical entities with identity state (internal, paginated)")
def entities(
    entity_type: str | None = Query(default=None),
    source: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    cross_source_only: bool = Query(default=False),
    has_ambiguous: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    return svc.entities(entity_type=entity_type, source=source, status=status_filter,
                        cross_source_only=cross_source_only, has_ambiguous=has_ambiguous,
                        limit=limit, offset=offset)


@router.get("/entities/{entity_type}/{entity_id}", summary="Canonical entity detail (internal)")
def entity_detail(
    entity_type: str, entity_id: str,
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    data = svc.entity_detail(entity_type, entity_id)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="entity not found")
    return data


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


@events_router.get("/{event_id}/interpreted-entities",
                   summary="Integrity-projected Event relationships (internal, 5B.2.5)")
def interpreted_entities(
    event_id: str,
    svc: EntityResolutionService = Depends(get_entity_resolution_service),
) -> dict[str, Any]:
    return svc.interpreted_relationships(event_id)
