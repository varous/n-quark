"""Internal operational-coverage + control surface for scheduled capture (Phase 2).

All routes are internal (under /v1/internal). Nothing here is public and no Shadow Ledger data is
redistributed. Control routes (sync/run) are gated by SCHEDULED_CAPTURE_ENABLED.
"""

from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from crawl_service.config import settings
from crawl_service.db import SessionLocal
from crawl_service.deps import get_scheduler
from crawl_service.models import ScheduledCaptureJob, TrackedEvent
from crawl_service.service import SchedulerService, _aware, _iso

router = APIRouter(prefix="/v1/internal/capture-schedule", tags=["capture-schedule (internal)"])


def _coverage(te: TrackedEvent, now: datetime, job: ScheduledCaptureJob | None) -> dict[str, Any]:
    next_at = _aware(te.next_capture_at)
    gap_hours = None
    if te.last_success_at is not None:
        gap_hours = round((now - _aware(te.last_success_at)).total_seconds() / 3600.0, 2)
    lock = None
    if job is not None and job.status == "RUNNING":
        lock = {"worker_id": job.worker_id, "lock_expires_at": _iso(_aware(job.lock_expires_at)),
                "expired": bool(job.lock_expires_at and _aware(job.lock_expires_at) < now)}
    return {
        "source": te.source, "source_record_id": te.source_record_id,
        "canonical_event_id": te.canonical_event_id, "city": te.city,
        "tracking_status": te.tracking_status,
        "next_capture_at": _iso(next_at), "cadence_reason": te.cadence_reason,
        "priority": te.priority, "priority_reason": te.priority_reason,
        "last_attempt_at": _iso(_aware(te.last_attempt_at)),
        "last_success_at": _iso(_aware(te.last_success_at)),
        "last_record_present_at": _iso(_aware(te.last_record_present_at)),
        "last_state_change_at": _iso(_aware(te.last_state_change_at)),
        "last_capture_status": te.last_capture_status,
        "consecutive_failures": te.consecutive_failures,
        "consecutive_absences": te.consecutive_absences,
        "capture_count": te.capture_count,
        "distinct_state_count": te.distinct_state_count,
        "transition_count": te.transition_count,
        "capture_gap_hours": gap_hours,
        "lock": lock,
    }


@router.get("", summary="List operational capture coverage (internal)")
def list_schedule(
    source: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    with SessionLocal() as s:
        stmt = select(TrackedEvent)
        if source:
            stmt = stmt.where(TrackedEvent.source == source)
        stmt = stmt.order_by(TrackedEvent.priority.desc()).limit(limit)
        tracked = s.execute(stmt).scalars().all()
        items = [_coverage(te, now, None) for te in tracked]
    return {"count": len(items), "events": items}


@router.get("/{source}/{source_record_id}", summary="Capture coverage for one event (internal)")
def get_schedule(source: str, source_record_id: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    with SessionLocal() as s:
        te = s.execute(
            select(TrackedEvent).where(
                TrackedEvent.source == source, TrackedEvent.source_record_id == source_record_id
            )
        ).scalar_one_or_none()
        if te is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not tracked")
        job = s.execute(
            select(ScheduledCaptureJob).where(
                ScheduledCaptureJob.source == source,
                ScheduledCaptureJob.source_record_id == source_record_id,
            ).order_by(ScheduledCaptureJob.created_at.desc())
        ).scalars().first()
        return _coverage(te, now, job)


@router.post("/sync", summary="Discover + enroll Boshow events for tracking (internal)")
async def sync_schedule(
    source: str = Query(default="boshow"),
    city: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    scheduler: SchedulerService = Depends(get_scheduler),
) -> dict[str, Any]:
    if not settings.scheduled_capture_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="scheduled capture disabled")
    url = f"{settings.signal_service_url}/v1/signals/ticketing/discover"
    params: dict[str, Any] = {"limit": limit}
    if city:
        params["city"] = city
    try:
        async with httpx.AsyncClient(timeout=settings.capture_http_timeout_seconds) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            refs = resp.json().get("event_refs", [])
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"discover failed: {exc}") from exc
    enrolled = scheduler.sync_from_refs(source, refs)
    return {"source": source, "discovered": len(refs), "enrolled": enrolled}


@router.post("/run", summary="Run one scheduling pass (internal)")
async def run_schedule(
    trace: bool = Query(default=False),
    limit: int | None = Query(default=None),
    scheduler: SchedulerService = Depends(get_scheduler),
) -> dict[str, Any]:
    if not settings.scheduled_capture_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="scheduled capture disabled")
    worker_id = f"api-{datetime.now(UTC).strftime('%H%M%S')}"
    return await scheduler.run_once(worker_id, limit=limit, trace=trace)
