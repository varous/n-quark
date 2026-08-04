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


def _job_dict(j: ScheduledCaptureJob) -> dict[str, Any]:
    return {
        "id": j.id, "source": j.source, "source_record_id": j.source_record_id,
        "canonical_event_id": j.canonical_event_id, "status": j.status, "priority": j.priority,
        "scheduled_at": _iso(_aware(j.scheduled_at)), "started_at": _iso(_aware(j.started_at)),
        "completed_at": _iso(_aware(j.completed_at)), "next_capture_at": _iso(_aware(j.next_capture_at)),
        "attempt_count": j.attempt_count, "consecutive_failures": j.consecutive_failures,
        "worker_id": j.worker_id, "lock_expires_at": _iso(_aware(j.lock_expires_at)),
        "result_code": j.result_code, "last_error_code": j.last_error_code,
        "created_at": _iso(_aware(j.created_at)), "updated_at": _iso(_aware(j.updated_at)),
    }


@router.get("/jobs", summary="List scheduled capture jobs (internal, paginated)")
def list_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    source: str | None = Query(default=None),
    expired_lock: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    now = datetime.now(UTC)
    with SessionLocal() as s:
        stmt = select(ScheduledCaptureJob)
        if status_filter:
            stmt = stmt.where(ScheduledCaptureJob.status == status_filter)
        if source:
            stmt = stmt.where(ScheduledCaptureJob.source == source)
        total = len(s.execute(stmt).scalars().all())
        stmt = stmt.order_by(ScheduledCaptureJob.created_at.desc()).offset(offset).limit(limit)
        jobs = s.execute(stmt).scalars().all()
    items = [_job_dict(j) for j in jobs]
    if expired_lock:
        items = [it for it in items
                 if it["status"] == "RUNNING" and it["lock_expires_at"]
                 and datetime.fromisoformat(it["lock_expires_at"]) < now]
    return {"count": total, "limit": limit, "offset": offset, "jobs": items}


@router.get("/jobs/{job_id}", summary="One scheduled capture job (internal)")
def get_job(job_id: str) -> dict[str, Any]:
    with SessionLocal() as s:
        j = s.get(ScheduledCaptureJob, job_id)
        if j is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="job not found")
        data = _job_dict(j)
        data["detail"] = j.detail
        return data


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
    params: dict[str, Any] = {"limit": limit, "source": source}  # per-source provider (Phase 3)
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


@router.post("/capture-now", summary="Targeted capture of one tracked event via the normal path (internal)")
async def capture_now(
    source: str = Query(...),
    source_record_id: str = Query(...),
    canonical_event_id: str | None = Query(default=None),
    scheduler: SchedulerService = Depends(get_scheduler),
) -> dict[str, Any]:
    if not settings.scheduled_capture_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="scheduled capture disabled")
    return await scheduler.capture_now(source=source, source_record_id=source_record_id,
                                       canonical_event_id=canonical_event_id)


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
