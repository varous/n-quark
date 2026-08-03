"""SchedulerService — selection, lease-locking, retry and health for scheduled capture (Phase 2).

Pure DB + an injected async Capturer (the only outbound boundary). All datetime comparisons are done
in Python against UTC-aware values so the logic is identical on SQLite (tests) and Postgres (prod).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from crawl_service.cadence import CadenceConfig, compute_cadence
from crawl_service.capturer import Capturer
from crawl_service.classification import (
    SUCCESS_RECORD_ABSENT,
    SUCCESS_RECORD_PRESENT,
    decide_retry,
)
from crawl_service.config import Settings, settings
from crawl_service.models import ScheduledCaptureJob, TrackedEvent
from crawl_service.priority import compute_priority

ACTIVE_JOB_STATUSES = ("PENDING", "RUNNING")
TRACKABLE = ("ACTIVE", "POST_EVENT")


def _uuid() -> str:
    return uuid.uuid4().hex


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class SchedulerService:
    def __init__(self, session_factory, capturer: Capturer, config: Settings | None = None) -> None:
        self._sf = session_factory
        self._capturer = capturer
        self._cfg = config or settings
        self._cadence = CadenceConfig(
            far_future_hours=self._cfg.cadence_far_future_hours,
            mid_hours=self._cfg.cadence_mid_hours,
            final_hours=self._cfg.cadence_final_hours,
            onsale_burst_hours=self._cfg.cadence_onsale_burst_hours,
            event_day_hours=self._cfg.cadence_event_day_hours,
        )

    # ---- enrollment ----------------------------------------------------------------------------
    def enroll(self, source, source_record_id, *, city=None, starts_at=None, on_sale_at=None,
               canonical_event_id=None, now=None) -> str | None:
        if source not in self._cfg.scheduled_capture_source_set:
            return None
        allow = self._cfg.city_allowlist_set
        if allow and city and city.lower() not in allow:
            return None
        now = now or datetime.now(UTC)
        with self._sf() as s, s.begin():
            existing = s.execute(
                select(TrackedEvent).where(
                    TrackedEvent.source == source, TrackedEvent.source_record_id == source_record_id
                )
            ).scalar_one_or_none()
            if existing is not None:
                # enrich known fields but never resurrect a stopped/terminal tracker here
                if city and not existing.city:
                    existing.city = city
                if starts_at and not existing.starts_at:
                    existing.starts_at = starts_at
                existing.updated_at = now
                return existing.id
            total = s.execute(select(TrackedEvent.id)).all()
            if len(total) >= self._cfg.scheduled_capture_max_tracked:
                return None
            te = TrackedEvent(
                id=_uuid(), source=source, source_record_id=source_record_id,
                canonical_event_id=canonical_event_id, city=city, starts_at=starts_at,
                on_sale_at=on_sale_at, tracking_status="ACTIVE", first_tracked_at=now,
                next_capture_at=None, priority=0, priority_reason={}, created_at=now, updated_at=now,
            )
            s.add(te)
            return te.id

    def sync_from_refs(self, source, refs, *, now=None) -> int:
        n = 0
        for ref in refs:
            if self.enroll(source, ref, now=now):
                n += 1
        return n

    # ---- job lifecycle -------------------------------------------------------------------------
    def generate_due_jobs(self, now: datetime) -> list[str]:
        created: list[str] = []
        with self._sf() as s, s.begin():
            tracked = s.execute(
                select(TrackedEvent).where(
                    TrackedEvent.source.in_(tuple(self._cfg.scheduled_capture_source_set) or ("",)),
                    TrackedEvent.tracking_status.in_(TRACKABLE),
                )
            ).scalars().all()
            for te in tracked:
                if te.next_capture_at is not None and _aware(te.next_capture_at) > now:
                    continue
                window = _iso(te.next_capture_at) or "initial"
                dedup = f"{te.source}:{te.source_record_id}:{window}"
                if s.execute(select(ScheduledCaptureJob.id).where(ScheduledCaptureJob.dedup_key == dedup)).first():
                    continue  # duplicate capture window (idempotent against duplicate cron)
                if s.execute(select(ScheduledCaptureJob.id).where(
                    ScheduledCaptureJob.source == te.source,
                    ScheduledCaptureJob.source_record_id == te.source_record_id,
                    ScheduledCaptureJob.status.in_(ACTIVE_JOB_STATUSES),
                )).first():
                    continue  # already an active job for this event
                score, reason, comps = compute_priority(
                    now, starts_at=_aware(te.starts_at), on_sale_at=_aware(te.on_sale_at),
                    last_state_change_at=_aware(te.last_state_change_at),
                    consecutive_failures=te.consecutive_failures, city=te.city,
                    city_allowlist=self._cfg.city_allowlist_set,
                )
                te.priority = score
                te.priority_reason = {"reason": reason, "components": comps}
                te.updated_at = now
                job = ScheduledCaptureJob(
                    id=_uuid(), dedup_key=dedup, source=te.source,
                    source_record_id=te.source_record_id, canonical_event_id=te.canonical_event_id,
                    status="PENDING", priority=score, scheduled_at=(_aware(te.next_capture_at) or now),
                    attempt_count=0, consecutive_failures=te.consecutive_failures, detail={},
                    created_at=now, updated_at=now,
                )
                s.add(job)
                created.append(job.id)
        return created

    def recover_expired_locks(self, now: datetime) -> int:
        with self._sf() as s:
            rows = s.execute(
                select(ScheduledCaptureJob.id, ScheduledCaptureJob.lock_expires_at)
                .where(ScheduledCaptureJob.status == "RUNNING")
            ).all()
            expired = [jid for jid, le in rows if le is not None and _aware(le) < now]
            for jid in expired:
                s.execute(
                    update(ScheduledCaptureJob).where(
                        ScheduledCaptureJob.id == jid, ScheduledCaptureJob.status == "RUNNING"
                    ).values(status="PENDING", worker_id=None, lock_expires_at=None, updated_at=now)
                )
            s.commit()
        return len(expired)

    def claim_jobs(self, now: datetime, worker_id: str, limit: int) -> list[str]:
        ttl = self._cfg.scheduled_capture_lock_ttl_seconds
        claimed: list[str] = []
        with self._sf() as s:
            rows = s.execute(
                select(ScheduledCaptureJob.id, ScheduledCaptureJob.scheduled_at)
                .where(ScheduledCaptureJob.status == "PENDING")
                .order_by(ScheduledCaptureJob.priority.desc(), ScheduledCaptureJob.scheduled_at.asc())
            ).all()
            due = [jid for jid, sched in rows if _aware(sched) <= now]
            for jid in due:
                if len(claimed) >= limit:
                    break
                # Atomic compare-and-swap: only the worker whose UPDATE matches PENDING wins.
                res = s.execute(
                    update(ScheduledCaptureJob).where(
                        ScheduledCaptureJob.id == jid, ScheduledCaptureJob.status == "PENDING"
                    ).values(
                        status="RUNNING", worker_id=worker_id,
                        lock_expires_at=now + timedelta(seconds=ttl), started_at=now,
                        attempt_count=ScheduledCaptureJob.attempt_count + 1, updated_at=now,
                    )
                )
                if res.rowcount == 1:
                    claimed.append(jid)
            s.commit()
        return claimed

    async def process_job(self, job_id: str, now: datetime) -> dict[str, Any]:
        with self._sf() as s:
            job = s.get(ScheduledCaptureJob, job_id)
            tracked = s.execute(
                select(TrackedEvent).where(
                    TrackedEvent.source == job.source,
                    TrackedEvent.source_record_id == job.source_record_id,
                )
            ).scalar_one_or_none()
            source, sid = job.source, job.source_record_id
            canonical = tracked.canonical_event_id if tracked else job.canonical_event_id
            attempt = job.attempt_count

        steps = ["event_selected", "priority_calculated", "cadence_previously_set", "job_claimed"]
        outcome = await self._capturer.capture(
            source=source, source_record_id=sid, canonical_event_id=canonical
        )
        steps += ["source_capture_attempted", "result_classified"]
        decision = decide_retry(
            outcome.result_code, attempt=attempt,
            max_attempts=self._cfg.scheduled_capture_max_attempts,
            parser_retry_limit=self._cfg.scheduled_capture_parser_retry_limit,
            retry_after_seconds=outcome.retry_after_seconds,
            backoff_base=self._cfg.scheduled_capture_backoff_base_seconds,
            backoff_cap=self._cfg.scheduled_capture_backoff_max_seconds,
        )

        with self._sf() as s, s.begin():
            job = s.get(ScheduledCaptureJob, job_id)
            tracked = s.execute(
                select(TrackedEvent).where(
                    TrackedEvent.source == source, TrackedEvent.source_record_id == sid
                )
            ).scalar_one_or_none()
            job.result_code = outcome.result_code
            job.completed_at = now
            job.lock_expires_at = None
            job.updated_at = now
            tracked.last_attempt_at = now
            tracked.last_capture_status = outcome.result_code

            next_at: datetime | None = None
            cadence_reason = None
            if outcome.result_code == SUCCESS_RECORD_PRESENT:
                steps += ["existing_ingest_invoked", "shadow_ledger_result_received"]
                tracked.consecutive_failures = 0
                tracked.consecutive_absences = 0
                tracked.last_success_at = now
                tracked.last_record_present_at = now
                tracked.capture_count += 1
                if outcome.canonical_event_id:
                    tracked.canonical_event_id = outcome.canonical_event_id
                    job.canonical_event_id = outcome.canonical_event_id
                sr = outcome.shadow_result or {}
                if sr and not sr.get("noop") and sr.get("persisted"):
                    tracked.distinct_state_count += 1
                trs = sr.get("transitions") or []
                if trs:
                    tracked.transition_count += len(trs)
                    tracked.last_state_change_at = now
                next_at, cadence_reason = compute_cadence(
                    now, starts_at=_aware(tracked.starts_at), on_sale_at=_aware(tracked.on_sale_at),
                    tracking_status=tracked.tracking_status, config=self._cadence,
                )
                job.status = "SUCCEEDED"
            elif outcome.result_code == SUCCESS_RECORD_ABSENT:
                steps += ["authoritative_absence_recorded"]
                tracked.consecutive_failures = 0
                tracked.consecutive_absences += 1
                tracked.capture_count += 1
                tracked.last_success_at = now  # the request succeeded
                next_at, cadence_reason = compute_cadence(
                    now, starts_at=_aware(tracked.starts_at), on_sale_at=_aware(tracked.on_sale_at),
                    tracking_status=tracked.tracking_status, config=self._cadence,
                )
                job.status = "SUCCEEDED"
            elif decision.terminal:
                tracked.consecutive_failures += 1
                tracked.tracking_status = "NEEDS_REVIEW" if decision.needs_review else "STOPPED"
                job.status = "FAILED_TERMINAL"
                job.last_error_code = outcome.result_code
                cadence_reason = "terminal"
            else:  # retryable failure -> reschedule with backoff, NEVER absence
                tracked.consecutive_failures += 1
                next_at = now + timedelta(seconds=decision.backoff_seconds or self._cfg.scheduled_capture_backoff_base_seconds)
                cadence_reason = "retry_backoff"
                job.status = "FAILED_RETRYABLE"
                job.last_error_code = outcome.result_code

            if job.status in ("SUCCEEDED", "FAILED_RETRYABLE"):
                tracked.next_capture_at = next_at
                tracked.cadence_reason = cadence_reason
                job.next_capture_at = next_at
                if next_at is None and job.status == "SUCCEEDED":
                    tracked.tracking_status = "STOPPED"  # post-event follow-ups complete
            else:
                tracked.next_capture_at = None
                tracked.cadence_reason = cadence_reason
            job.consecutive_failures = tracked.consecutive_failures
            tracked.updated_at = now
            steps += ["health_record_updated", "next_capture_scheduled"]

            trace = {
                "source": source, "source_record_id": sid,
                "canonical_event_id": tracked.canonical_event_id,
                "result_code": outcome.result_code, "job_status": job.status,
                "attempt": attempt, "steps": steps,
                "next_capture_at": _iso(next_at), "cadence_reason": cadence_reason,
                "priority": tracked.priority, "priority_reason": tracked.priority_reason,
                "shadow": None if not outcome.shadow_result else {
                    "noop": outcome.shadow_result.get("noop"),
                    "transitions": [t.get("transition_type") for t in (outcome.shadow_result.get("transitions") or [])],
                },
                "consecutive_absences": tracked.consecutive_absences,
                "consecutive_failures": tracked.consecutive_failures,
            }
        return trace

    async def run_once(self, worker_id: str, *, now: datetime | None = None,
                       limit: int | None = None, trace: bool = False) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        limit = limit or self._cfg.scheduled_capture_max_jobs
        recovered = self.recover_expired_locks(now)
        created = self.generate_due_jobs(now)
        claimed = self.claim_jobs(now, worker_id, limit)
        runs = [await self.process_job(jid, now) for jid in claimed]
        summary = {
            "worker_id": worker_id, "recovered_locks": recovered,
            "jobs_created": len(created), "jobs_claimed": len(claimed), "processed": len(runs),
        }
        if trace:
            summary["runs"] = runs
        return summary
