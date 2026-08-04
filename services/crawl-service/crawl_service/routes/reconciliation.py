"""Internal cross-platform reconciliation endpoints (Phase 3). Internal only; flag-gated."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from crawl_service.config import settings
from crawl_service.db import SessionLocal
from crawl_service.deps import get_probe_service, get_reconciliation_service
from crawl_service.models import EventMatchCandidate, TrackedEvent
from crawl_service.reconciliation.probe import ProbeService
from crawl_service.reconciliation.service import ReconciliationService

router = APIRouter(prefix="/v1/internal/reconciliation", tags=["reconciliation (internal)"])
events_router = APIRouter(prefix="/v1/internal/events", tags=["reconciliation (internal)"])


def _match_dict(m: EventMatchCandidate) -> dict[str, Any]:
    return {
        "id": m.id, "status": m.match_status, "score": m.match_score, "reason": m.reason_code,
        "left": {"source": m.left_source, "source_record_id": m.left_source_record_id,
                 "canonical_event_id": m.left_canonical_event_id},
        "right": {"source": m.right_source, "source_record_id": m.right_source_record_id,
                  "canonical_event_id": m.right_canonical_event_id},
        "component_scores": m.component_scores, "supporting_signals": m.supporting_signals,
        "contradicting_signals": m.contradicting_signals, "matcher_version": m.matcher_version,
    }


@router.post("/probe", summary="Compare candidate second sources on a live cohort (internal)")
async def probe(
    sources: str = Query(default="district,skillbox"),
    sample: int = Query(default=10, ge=1, le=50),
    probe_svc: ProbeService = Depends(get_probe_service),
) -> dict[str, Any]:
    if not settings.reconciliation_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="reconciliation disabled")
    return await probe_svc.compare([s.strip() for s in sources.split(",") if s.strip()], sample=sample)


@router.post("/run", summary="Generate + score cross-platform match candidates (internal)")
async def run(
    trace: bool = Query(default=False),
    svc: ReconciliationService = Depends(get_reconciliation_service),
) -> dict[str, Any]:
    if not settings.reconciliation_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="reconciliation disabled")
    return await svc.run(trace=trace)


@router.get("/matches", summary="List match candidates (internal)")
def list_matches(
    status_filter: str | None = Query(default=None, alias="status"),
    left_source: str | None = Query(default=None),
    right_source: str | None = Query(default=None),
    min_score: float = Query(default=0.0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    with SessionLocal() as s:
        stmt = select(EventMatchCandidate).where(EventMatchCandidate.match_score >= min_score)
        if status_filter:
            stmt = stmt.where(EventMatchCandidate.match_status == status_filter)
        if left_source:
            stmt = stmt.where(EventMatchCandidate.left_source == left_source)
        if right_source:
            stmt = stmt.where(EventMatchCandidate.right_source == right_source)
        stmt = stmt.order_by(EventMatchCandidate.match_score.desc()).limit(limit)
        rows = s.execute(stmt).scalars().all()
        return {"count": len(rows), "matches": [_match_dict(m) for m in rows]}


@router.get("/matches/{match_id}", summary="Match detail + field reconciliation (internal)")
async def match_detail(
    match_id: str, svc: ReconciliationService = Depends(get_reconciliation_service),
) -> dict[str, Any]:
    with SessionLocal() as s:
        m = s.get(EventMatchCandidate, match_id)
        if m is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="match not found")
        data = _match_dict(m)
        left_cid, right_cid = m.left_canonical_event_id, m.right_canonical_event_id
    if m.match_status in ("MATCHED", "POSSIBLE_MATCH") and left_cid and right_cid:
        data["field_reconciliation"] = await svc.reconcile_pair(left_cid, right_cid)
        data["source_comparison"] = await svc.source_price_availability(left_cid, right_cid)
    return data


@router.get("/metrics", summary="Reconciliation metrics + overlap (internal)")
def metrics(right_source: str | None = Query(default=None)) -> dict[str, Any]:
    rs = right_source or settings.second_source_name
    with SessionLocal() as s:
        by_status = dict(s.execute(
            select(EventMatchCandidate.match_status, func.count())
            .group_by(EventMatchCandidate.match_status)).all())
        right_events = s.execute(
            select(func.count()).select_from(TrackedEvent).where(TrackedEvent.source == rs)).scalar() or 0
        matched_right = s.execute(
            select(func.count(func.distinct(EventMatchCandidate.right_canonical_event_id)))
            .where(EventMatchCandidate.right_source == rs,
                   EventMatchCandidate.match_status.in_(("MATCHED", "POSSIBLE_MATCH")))).scalar() or 0
    total = sum(by_status.values())
    return {
        "by_status": by_status,
        "auto_match": by_status.get("MATCHED", 0), "possible_match": by_status.get("POSSIBLE_MATCH", 0),
        "conflict": by_status.get("CONFLICT", 0), "not_matched": by_status.get("NOT_MATCHED", 0),
        "match_rate": round(by_status.get("MATCHED", 0) / total, 4) if total else 0.0,
        "second_source_events": right_events,
        "second_source_with_counterpart": matched_right,
        "overlap_rate": round(matched_right / right_events, 4) if right_events else 0.0,
    }


@events_router.get("/{event_id}/source-records", summary="Source listings linked to an event (internal)")
def source_records(
    event_id: str, svc: ReconciliationService = Depends(get_reconciliation_service),
) -> dict[str, Any]:
    return svc.source_records(event_id)
