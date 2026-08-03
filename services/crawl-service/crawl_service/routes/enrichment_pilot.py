"""Internal live-enrichment pilot + source-value reports (Phase 2.2). Internal only; flag-gated."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from crawl_service.config import settings
from crawl_service.db import SessionLocal
from crawl_service.deps import get_pilot_service
from crawl_service.enrichment.pilot.reports import source_value_report, venue_coverage_report
from crawl_service.enrichment.pilot.service import PilotService
from crawl_service.models import EnrichmentRun

router = APIRouter(prefix="/v1/internal/enrichment", tags=["enrichment-pilot (internal)"])


def _iso(dt):
    return dt.isoformat() if dt else None


@router.post("/pilot/run", summary="Run one live public-page enrichment pilot pass (internal)")
async def run_pilot(
    trace: bool = Query(default=False),
    pilot: PilotService = Depends(get_pilot_service),
) -> dict[str, Any]:
    if not (settings.capture_enrichment_pilot_enabled and settings.capture_enrichment_public_page_enabled):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="enrichment pilot disabled (needs pilot + public-page flags)")
    return await pilot.run(trace=trace)


@router.get("/pilot/runs", summary="List persisted pilot runs (internal)")
def list_runs(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    with SessionLocal() as s:
        rows = s.execute(select(EnrichmentRun).order_by(EnrichmentRun.started_at.desc()).limit(limit)).scalars().all()
        return {"count": len(rows), "runs": [
            {"id": r.id, "surface": r.surface, "status": r.status, "started_at": _iso(r.started_at),
             "events_selected": r.events_selected, "pages_attempted": r.pages_attempted,
             "valid_event_pages": r.pages_retrieved, "candidates_created": r.candidates_created,
             "incremental": r.resolutions_changed, "conflicts": r.conflicts_found,
             "parser_failures": r.parser_failures, "recommendation": (r.metrics or {}).get("recommendation")}
            for r in rows]}


@router.get("/source-value", summary="Field-level source-value report (internal)")
def source_value(
    surface: str | None = Query(default=None),
    field: str | None = Query(default=None),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
) -> dict[str, Any]:
    return source_value_report(SessionLocal, surface=surface, field=field, start=start, end=end)


@router.get("/venue-coverage", summary="Venue-resolution coverage report (internal)")
def venue_coverage() -> dict[str, Any]:
    return venue_coverage_report(SessionLocal)
