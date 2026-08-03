"""Internal enrichment inspection + manual re-resolve (Phase 2.1). Internal only; no public API."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from crawl_service.config import settings
from crawl_service.deps import get_enricher
from crawl_service.enrichment.service import EnrichmentService

router = APIRouter(prefix="/v1/internal/events", tags=["enrichment (internal)"])


@router.get("/{event_id}/enrichment", summary="Resolved fields + candidate provenance (internal)")
def get_enrichment(event_id: str, enricher: EnrichmentService = Depends(get_enricher)) -> dict[str, Any]:
    return enricher.get_enrichment(event_id)


@router.post("/{event_id}/enrichment/resolve", summary="Re-run enrichment for one event (internal)")
async def resolve_enrichment(
    event_id: str,
    source: str = Query(default="boshow"),
    source_record_id: str | None = Query(default=None),
    trace: bool = Query(default=False),
    enricher: EnrichmentService = Depends(get_enricher),
) -> dict[str, Any]:
    if not settings.capture_enrichment_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="enrichment disabled")
    return await enricher.enrich(
        canonical_event_id=event_id, source=source, source_record_id=source_record_id, trace=trace
    )
