"""Demand refresh scheduler (Phase 5A) — a persisted, lease-locked, restart-safe job queue.

Reuses crawl-service's proven pattern (dedup on a refresh window, lease lock, attempt/backoff, terminal
after a budget) rather than a new scheduling architecture. All state lives in ``demand_refresh_job`` in
Postgres, so a restart resumes idempotently. Jobs use known channel ids (channels.list / videos.list) —
never search — and one job's failure is isolated from the rest.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from artist_intelligence_service import identity as idlib
from artist_intelligence_service.config import settings
from artist_intelligence_service.models import ArtistExternalIdentity, DemandRefreshJob
from artist_intelligence_service.providers.base import PROVIDER_YOUTUBE, RESOLVED
from artist_intelligence_service.service import DemandService

JOB_CHANNEL = "YOUTUBE_CHANNEL_SNAPSHOT"
JOB_VIDEO = "YOUTUBE_VIDEO_SNAPSHOT"

_INTERVALS = {
    JOB_CHANNEL: lambda: settings.youtube_channel_refresh_interval_seconds,
    JOB_VIDEO: lambda: settings.youtube_video_refresh_interval_seconds,
}


def _now() -> datetime:
    return datetime.now(UTC)


def _window(now: datetime, interval_s: int) -> str:
    """The current refresh window bucket — dedups duplicate enqueue within one cadence period."""
    epoch = int(now.timestamp()) // max(interval_s, 1)
    return str(epoch)


class DemandScheduler:
    def __init__(self, service: DemandService | None = None) -> None:
        self.service = service or DemandService()

    # ---- enqueue -----------------------------------------------------------------------------
    def enqueue_due(self, db: Session, *, now: datetime | None = None) -> dict[str, int]:
        """Ensure a job exists for each RESOLVED YouTube identity in the current cadence window.

        Idempotent: the unique ``dedup_key`` means a duplicate enqueue in the same window is ignored."""
        now = now or _now()
        resolved = db.execute(
            select(ArtistExternalIdentity).where(
                ArtistExternalIdentity.provider == PROVIDER_YOUTUBE,
                ArtistExternalIdentity.identity_type == "CHANNEL_ID",
                ArtistExternalIdentity.status == RESOLVED,
            )
        ).scalars().all()
        created = 0
        for ident in resolved:
            for job_type in (JOB_CHANNEL, JOB_VIDEO):
                window = _window(now, _INTERVALS[job_type]())
                dedup = f"{ident.canonical_artist_id}|{PROVIDER_YOUTUBE}|{job_type}|{window}"
                exists = db.execute(
                    select(DemandRefreshJob.id).where(DemandRefreshJob.dedup_key == dedup)
                ).scalar_one_or_none()
                if exists is not None:
                    continue
                db.add(DemandRefreshJob(
                    id=idlib.new_id(dedup), dedup_key=dedup,
                    canonical_artist_id=ident.canonical_artist_id, provider=PROVIDER_YOUTUBE,
                    job_type=job_type, external_identity_id=ident.id, status="PENDING",
                    scheduled_at=now, created_at=now, updated_at=now, detail={}))
                created += 1
        db.flush()
        return {"resolved_identities": len(resolved), "jobs_created": created}

    # ---- claim (lease) -----------------------------------------------------------------------
    def claim(self, db: Session, *, worker_id: str, now: datetime | None = None,
              limit: int | None = None) -> list[DemandRefreshJob]:
        now = now or _now()
        limit = limit or settings.demand_scheduler_batch_size
        rows = db.execute(
            select(DemandRefreshJob).where(
                DemandRefreshJob.scheduled_at <= now,
                or_(
                    DemandRefreshJob.status == "PENDING",
                    DemandRefreshJob.status == "FAILED_RETRYABLE",
                    # reclaim an expired lease (a worker died mid-job)
                    (DemandRefreshJob.status == "RUNNING") & (DemandRefreshJob.lock_expires_at < now),
                ),
            ).order_by(DemandRefreshJob.scheduled_at).limit(limit)
        ).scalars().all()
        claimed = []
        for job in rows:
            job.status = "RUNNING"
            job.worker_id = worker_id
            job.started_at = now
            job.lock_expires_at = now + timedelta(seconds=settings.demand_scheduler_lock_ttl_seconds)
            job.attempt_count += 1
            job.updated_at = now
            claimed.append(job)
        db.flush()
        return claimed

    # ---- execute one job (failure-isolated) --------------------------------------------------
    async def execute(self, db: Session, job: DemandRefreshJob, *, now: datetime | None = None) -> str:
        now = now or _now()
        try:
            if job.job_type == JOB_CHANNEL:
                result = await self.service.snapshot_youtube(
                    db, job.canonical_artist_id, include_channel=True, include_videos=False,
                    observed_at=now)
            elif job.job_type == JOB_VIDEO:
                result = await self.service.snapshot_youtube(
                    db, job.canonical_artist_id, include_channel=False, include_videos=True,
                    observed_at=now)
            else:
                return self._terminal(job, now, "UNKNOWN_JOB_TYPE")
            if result.get("status") == "NO_RESOLVED_IDENTITY":
                return self._terminal(job, now, "NO_RESOLVED_IDENTITY")
            return self._succeed(job, now, result)
        except Exception as exc:  # noqa: BLE001 — isolate: one job's failure never stops the others
            return self._fail(job, now, exc)

    def _succeed(self, job: DemandRefreshJob, now: datetime, result: dict[str, Any]) -> str:
        job.status = "SUCCEEDED"
        job.result_code = "OK"
        job.completed_at = now
        job.consecutive_failures = 0
        job.lock_expires_at = None
        job.next_refresh_at = now + timedelta(seconds=_INTERVALS[job.job_type]())
        job.detail = {**(job.detail or {}), "last_result": result}
        job.updated_at = now
        return "SUCCEEDED"

    def _fail(self, job: DemandRefreshJob, now: datetime, exc: Exception) -> str:
        job.consecutive_failures += 1
        job.last_error_code = type(exc).__name__
        job.detail = {**(job.detail or {}), "last_error": str(exc)[:500]}
        if job.attempt_count >= settings.demand_scheduler_max_attempts:
            return self._terminal(job, now, "MAX_ATTEMPTS")
        backoff = min(settings.demand_scheduler_backoff_base_seconds * (2 ** (job.attempt_count - 1)),
                      settings.demand_scheduler_backoff_max_seconds)
        job.status = "FAILED_RETRYABLE"
        job.result_code = "RETRY"
        job.scheduled_at = now + timedelta(seconds=backoff)
        job.lock_expires_at = None
        job.updated_at = now
        return "FAILED_RETRYABLE"

    def _terminal(self, job: DemandRefreshJob, now: datetime, code: str) -> str:
        job.status = "FAILED_TERMINAL"
        job.result_code = code
        job.completed_at = now
        job.lock_expires_at = None
        job.updated_at = now
        return "FAILED_TERMINAL"

    # ---- one full pass -----------------------------------------------------------------------
    async def run_once(self, db: Session, *, worker_id: str = "scheduler",
                       now: datetime | None = None) -> dict[str, Any]:
        now = now or _now()
        enq = self.enqueue_due(db, now=now)
        claimed = self.claim(db, worker_id=worker_id, now=now)
        outcomes: dict[str, int] = {}
        for job in claimed:
            code = await self.execute(db, job, now=now)
            outcomes[code] = outcomes.get(code, 0) + 1
            db.flush()
        db.commit()
        return {"enqueued": enq, "claimed": len(claimed), "outcomes": outcomes}
