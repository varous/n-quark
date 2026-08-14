"""Demand refresh + acquisition scheduler (Phase 5A, extended in 5A.3).

A persisted, lease-locked, restart-safe job queue in ``demand_refresh_job`` (Postgres, so a restart
resumes idempotently). 5A.3 adds:
- new job types beyond channel/video refresh — YouTube IDENTITY discovery and one-time catalogue BACKFILL;
- an acquisition PRIORITY class ordered before scheduled_at when claiming (scarce quota → irreplaceable
  work first) — operational only, never an artist-value score;
- adaptive, event-aware refresh cadence chosen at execute time (config-driven, deterministic);
- quota-reserve awareness: QUOTA_EXHAUSTED DEFERS work (reschedules) instead of failing or invalidating;
- typed outcomes distinguishing authoritative NOT_FOUND (may invalidate) from transient/quota failures.

Repeated observation uses known channel/video ids (channels.list / videos.list / videos.list batch),
never search. Search is reserved for identity discovery. One job's failure is isolated from the rest.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from artist_intelligence_service import cadence as cad
from artist_intelligence_service import identity as idlib
from artist_intelligence_service.config import settings
from artist_intelligence_service.models import (
    ArtistDemandObservation,
    ArtistExternalIdentity,
    DemandRefreshJob,
    YouTubeVideo,
)
from artist_intelligence_service.crawl_client import CrawlServiceClient
from artist_intelligence_service.graph_client import GraphServiceClient
from artist_intelligence_service.providers.base import AMBIGUOUS, PROVIDER_YOUTUBE, RESOLVED, UNRESOLVED
from artist_intelligence_service.service import DemandService

JOB_CHANNEL = "YOUTUBE_CHANNEL_SNAPSHOT"
JOB_VIDEO = "YOUTUBE_VIDEO_SNAPSHOT"
JOB_IDENTITY = "YOUTUBE_IDENTITY_DISCOVERY"       # 5A.3: search → verify → resolve
JOB_CATALOGUE = "YOUTUBE_CATALOGUE_BACKFILL"      # 5A.3: one-time bounded uploads → registry

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
    def __init__(self, service: DemandService | None = None,
                 graph: GraphServiceClient | None = None,
                 crawl: CrawlServiceClient | None = None) -> None:
        self.service = service or DemandService()
        self.graph = graph or GraphServiceClient()
        self.crawl = crawl or CrawlServiceClient()

    # ---- enqueue: recurring channel/video refresh of RESOLVED identities ----------------------
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
        # Phase 5B.1 — an operator-PAUSED watch target suspends recurring collection for that artist
        # (unless another active watch target keeps it live). Never deletes history; just stops scheduling.
        from artist_intelligence_service.watchlist import paused_canonical_artist_ids
        paused = paused_canonical_artist_ids(db)
        created = skipped = 0
        for ident in resolved:
            if ident.canonical_artist_id in paused:
                skipped += 1
                continue
            priority = self._artist_priority(db, ident.canonical_artist_id)
            for job_type in (JOB_CHANNEL, JOB_VIDEO):
                window = _window(now, _INTERVALS[job_type]())
                dedup = f"{ident.canonical_artist_id}|{PROVIDER_YOUTUBE}|{job_type}|{window}"
                if self._exists(db, dedup):
                    continue
                self._add_job(db, dedup=dedup, canonical_artist_id=ident.canonical_artist_id,
                              job_type=job_type, external_identity_id=ident.id, priority=priority,
                              now=now)
                created += 1
        db.flush()
        return {"resolved_identities": len(resolved), "jobs_created": created,
                "paused_artists_skipped": skipped}

    # ---- enqueue: 5A.3 identity discovery + catalogue backfill (used by onboard/backfill) ------
    def enqueue_identity_discovery(self, db: Session, canonical_artist_id: str, *,
                                   display_name: str, priority: int = cad.P4_GLOBAL_CANDIDATE,
                                   now: datetime | None = None) -> bool:
        """Queue an identity-discovery job for a canonical artist lacking a RESOLVED YouTube identity.
        Idempotent while an active job exists; returns True if a new job was created."""
        now = now or _now()
        dedup = f"{canonical_artist_id}|{PROVIDER_YOUTUBE}|{JOB_IDENTITY}"
        if self._active_exists(db, canonical_artist_id, JOB_IDENTITY) or self._exists(db, dedup):
            return False
        self._add_job(db, dedup=dedup, canonical_artist_id=canonical_artist_id, job_type=JOB_IDENTITY,
                      external_identity_id=None, priority=priority, now=now,
                      detail={"display_name": display_name})
        db.flush()
        return True

    def enqueue_catalogue_backfill(self, db: Session, canonical_artist_id: str, *,
                                   external_identity_id: str | None = None,
                                   priority: int = cad.P2_CONFIRMED_OR_STRONG,
                                   now: datetime | None = None) -> bool:
        now = now or _now()
        dedup = f"{canonical_artist_id}|{PROVIDER_YOUTUBE}|{JOB_CATALOGUE}"
        if self._active_exists(db, canonical_artist_id, JOB_CATALOGUE) or self._exists(db, dedup):
            return False
        self._add_job(db, dedup=dedup, canonical_artist_id=canonical_artist_id, job_type=JOB_CATALOGUE,
                      external_identity_id=external_identity_id, priority=priority, now=now)
        db.flush()
        return True

    async def enqueue_identity_reattempts(self, db: Session, *,
                                          now: datetime | None = None) -> dict[str, int | bool]:
        """5B.2.7 §9/§10 — keep AMBIGUOUS/UNRESOLVED identities SCHEDULABLE.

        A successful HTTP identity job is not terminal for the identity: an unresolved/ambiguous artist is
        re-enqueued on a status-based cadence (UNRESOLVED → backoff retry; AMBIGUOUS → slower re-resolution)
        in a fresh dedup window. Eligibility is gated on the crawl product registry so invalid canonicals
        (quarantined / placeholder / compound / orphan) never enter active YouTube monitoring — and it
        fails CLOSED (re-enqueues nothing) if the registry is unavailable, rather than monitoring junk."""
        now = now or _now()
        idents = db.execute(
            select(ArtistExternalIdentity).where(
                ArtistExternalIdentity.provider == PROVIDER_YOUTUBE,
                ArtistExternalIdentity.identity_type == "CHANNEL_ID",
                ArtistExternalIdentity.status.in_((AMBIGUOUS, UNRESOLVED)))).scalars().all()
        if not idents:
            return {"eligible": 0, "jobs_created": 0}
        try:
            rows = await self.crawl.artists(limit=500)
            valid = {r.get("canonical_entity_id") or r.get("canonical_artist_id") or r.get("id")
                     for r in rows}
            valid.discard(None)
        except Exception:  # noqa: BLE001 — registry unavailable → fail closed (never monitor invalid)
            return {"eligible": len(idents), "jobs_created": 0, "registry_unavailable": True}
        from artist_intelligence_service.watchlist import paused_canonical_artist_ids
        paused = paused_canonical_artist_ids(db)
        created = skipped_invalid = 0
        for ident in idents:
            art = ident.canonical_artist_id
            if art not in valid:
                skipped_invalid += 1
                continue
            if art in paused:
                continue
            interval = (settings.youtube_identity_reattempt_ambiguous_seconds if ident.status == AMBIGUOUS
                        else settings.youtube_identity_reattempt_unresolved_seconds)
            window = _window(now, interval)
            dedup = f"{art}|{PROVIDER_YOUTUBE}|{JOB_IDENTITY}|reattempt|{window}"
            if self._active_exists(db, art, JOB_IDENTITY) or self._exists(db, dedup):
                continue
            display_name = (ident.identity_metadata or {}).get("query") or art
            self._add_job(db, dedup=dedup, canonical_artist_id=art, job_type=JOB_IDENTITY,
                          external_identity_id=None, priority=cad.P4_GLOBAL_CANDIDATE, now=now,
                          detail={"display_name": display_name, "reattempt": True})
            created += 1
        db.flush()
        return {"eligible": len(idents), "jobs_created": created, "skipped_invalid": skipped_invalid}

    # ---- enqueue helpers ---------------------------------------------------------------------
    def _exists(self, db: Session, dedup: str) -> bool:
        return db.execute(
            select(DemandRefreshJob.id).where(DemandRefreshJob.dedup_key == dedup)
        ).scalar_one_or_none() is not None

    def _active_exists(self, db: Session, artist: str, job_type: str) -> bool:
        return db.execute(
            select(DemandRefreshJob.id).where(
                DemandRefreshJob.canonical_artist_id == artist,
                DemandRefreshJob.job_type == job_type,
                DemandRefreshJob.status.in_(("PENDING", "RUNNING", "FAILED_RETRYABLE")))
        ).scalar_one_or_none() is not None

    def _add_job(self, db: Session, *, dedup: str, canonical_artist_id: str, job_type: str,
                 external_identity_id: str | None, priority: int, now: datetime,
                 detail: dict | None = None) -> DemandRefreshJob:
        job = DemandRefreshJob(
            id=idlib.new_id(dedup, now.isoformat()), dedup_key=dedup,
            canonical_artist_id=canonical_artist_id, provider=PROVIDER_YOUTUBE, job_type=job_type,
            external_identity_id=external_identity_id, status="PENDING", scheduled_at=now,
            created_at=now, updated_at=now, priority=priority, detail=detail or {})
        db.add(job)
        return job

    def _artist_priority(self, db: Session, artist: str) -> int:
        """Deterministic acquisition priority from cheap operational signals (no network in the hot path)."""
        days = self._days_to_upcoming_event(db, artist)
        refs = 1
        has_strong = False
        try:
            from artist_intelligence_service.candidates import evidence_classes_for
            classes = evidence_classes_for(db, artist)
            has_strong = bool(classes & {"CONFIRMED_LIVE_INDIA", "INDIA_DEMAND_OBSERVED"})
            refs = 2 if len(classes) >= 2 else 1
        except Exception:  # noqa: BLE001 — priority must never fail the enqueue
            pass
        return cad.priority_for(days_to_event=days, independent_references=refs,
                                has_confirmed_or_strong=has_strong, from_youtube_discovery=False)

    def _days_to_upcoming_event(self, db: Session, artist: str) -> float | None:
        """Best-effort upcoming-event proximity for event-aware cadence. Returns None unless event-aware
        scheduling is enabled AND a graph lookup succeeds — so tests/offline never hit the network."""
        return None  # populated by the event-aware path (graph) only when explicitly enabled

    # ---- claim (lease, priority-ordered) ------------------------------------------------------
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
                    (DemandRefreshJob.status == "RUNNING") & (DemandRefreshJob.lock_expires_at < now),
                ),
            ).order_by(DemandRefreshJob.priority, DemandRefreshJob.scheduled_at).limit(limit)
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

    # ---- execute one job (failure-isolated, typed outcomes) -----------------------------------
    async def execute(self, db: Session, job: DemandRefreshJob, *, now: datetime | None = None) -> str:
        now = now or _now()
        try:
            if job.job_type == JOB_CHANNEL:
                result = await self.service.snapshot_youtube(
                    db, job.canonical_artist_id, include_channel=True, include_videos=False,
                    observed_at=now)
            elif job.job_type == JOB_VIDEO:
                result = await self.service.snapshot_videos_registry(
                    db, job.canonical_artist_id, observed_at=now)
            elif job.job_type == JOB_IDENTITY:
                return await self._execute_identity(db, job, now)
            elif job.job_type == JOB_CATALOGUE:
                result = await self.service.backfill_catalogue(db, job.canonical_artist_id,
                                                               observed_at=now)
            else:
                return self._terminal(job, now, "UNKNOWN_JOB_TYPE")

            status = result.get("status")
            if status in ("NO_RESOLVED_IDENTITY", "PROVIDER_ID_NOT_FOUND"):
                return self._terminal(job, now, status)
            if status == "VERIFICATION_UNAVAILABLE":
                return self._fail(job, now, RuntimeError("verification unavailable (transient)"))
            days = await self._event_proximity_days(job.canonical_artist_id) \
                if job.job_type in (JOB_CHANNEL, JOB_VIDEO) else None
            return self._succeed(db, job, now, result, days_to_event=days)
        except Exception as exc:  # noqa: BLE001 — isolate: one job's failure never stops the others
            return self._fail(job, now, exc)

    async def _execute_identity(self, db: Session, job: DemandRefreshJob, now: datetime) -> str:
        display_name = (job.detail or {}).get("display_name") or job.canonical_artist_id
        # purpose: an already-AMBIGUOUS identity is corroboration ("ambiguity"), else "unresolved".
        ambiguous = db.execute(
            select(ArtistExternalIdentity.id).where(
                ArtistExternalIdentity.canonical_artist_id == job.canonical_artist_id,
                ArtistExternalIdentity.provider == PROVIDER_YOUTUBE,
                ArtistExternalIdentity.status == AMBIGUOUS).limit(1)).scalar_one_or_none() is not None
        from artist_intelligence_service.quota import (
            SEARCH_AMBIGUITY,
            SEARCH_UNRESOLVED,
            near_provider_reset,
        )
        purpose = SEARCH_AMBIGUITY if ambiguous else SEARCH_UNRESOLVED
        out = await self.service.discover_identity_for_artist(
            db, job.canonical_artist_id, display_name=display_name, purpose=purpose,
            others_have_backlog=(purpose == SEARCH_AMBIGUITY), near_reset=near_provider_reset(now=now))
        status = out.get("status")
        if status == "QUOTA_EXHAUSTED":
            return self._defer(job, now, "QUOTA_EXHAUSTED")   # never invalidate; retry when budget resets
        if status == RESOLVED:
            # a resolved identity earns a one-time catalogue backfill + recurring refresh (next pass).
            self.enqueue_catalogue_backfill(db, job.canonical_artist_id,
                                            external_identity_id=out.get("identity_id"), now=now)
            return self._succeed(db, job, now, out)
        if status == "VERIFICATION_UNAVAILABLE":
            return self._fail(job, now, RuntimeError("verification unavailable (transient)"))
        # AMBIGUOUS / UNRESOLVED are legitimate terminal outcomes for THIS discovery attempt: the identity
        # row records the outcome; a later pass may re-attempt with more evidence. Not a failure.
        return self._succeed(db, job, now, out)

    def _succeed(self, db: Session, job: DemandRefreshJob, now: datetime, result: dict[str, Any],
                 *, days_to_event: float | None = None) -> str:
        job.status = "SUCCEEDED"
        job.result_code = result.get("status") or "OK"
        job.completed_at = now
        job.consecutive_failures = 0
        job.lock_expires_at = None
        job.next_refresh_at = self._next_refresh_for(db, job, now, days_to_event=days_to_event)
        job.detail = {**(job.detail or {}), "last_result": result}
        job.updated_at = now
        return "SUCCEEDED"

    def _next_refresh_for(self, db: Session, job: DemandRefreshJob, now: datetime, *,
                          days_to_event: float | None = None) -> datetime | None:
        """Adaptive, deterministic cadence (config-driven). One-time jobs have no recurring refresh.

        An upcoming Indian event (``days_to_event`` from the live graph) tightens the interval via the
        event bands, taking the min with the base cadence — so event proximity always increases cadence."""
        if job.job_type == JOB_CHANNEL:
            base = cad.channel_cadence_seconds(
                days_to_event=days_to_event,
                recently_active=self._recently_active(db, job.canonical_artist_id, now))
            band = cad.event_band_seconds(days_to_event)
            secs = min(base, band) if band is not None else base
            return now + timedelta(seconds=secs)
        if job.job_type == JOB_VIDEO:
            base = cad.video_cadence_seconds(self._freshest_video_age_days(db, job.canonical_artist_id, now))
            band = cad.event_band_seconds(days_to_event)
            secs = min(base, band) if band is not None else base
            return now + timedelta(seconds=secs)
        return None  # identity / catalogue are one-time (a fresh job is enqueued when needed)

    async def _event_proximity_days(self, artist: str) -> float | None:
        """Days to the nearest upcoming Indian event for a canonical artist, read LIVE from the graph
        (FEATURES neighbours). Bounded, best-effort: disabled by flag → None; any graph failure → None
        (safe degrade to normal cadence). Event data is never duplicated into this service."""
        if not settings.event_aware_cadence_enabled:
            return None
        try:
            neighbors = await self.graph.neighbors(artist, direction="in", relationship="FEATURES")
        except Exception:  # noqa: BLE001 — graph outage must not break scheduling
            return None
        from artist_intelligence_service.supply import _parse_dt
        now = _now()
        best: float | None = None
        for nb in neighbors[:200]:
            props = ((nb.get("node") or {}).get("properties") or {})
            starts = _parse_dt(props.get("starts_at") or props.get("start_time"))
            if starts is None:
                continue
            days = (starts - now).total_seconds() / 86400.0
            if -3 <= days <= settings.event_proximity_max_days and (best is None or days < best):
                best = days
        return best

    def _recently_active(self, db: Session, artist: str, now: datetime) -> bool:
        cutoff = now - timedelta(days=3)
        row = db.execute(
            select(ArtistDemandObservation.id).where(
                ArtistDemandObservation.canonical_artist_id == artist,
                ArtistDemandObservation.observed_at >= cutoff).limit(1)
        ).scalar_one_or_none()
        return row is not None

    def _freshest_video_age_days(self, db: Session, artist: str, now: datetime) -> float:
        from sqlalchemy import func
        newest = db.execute(
            select(func.max(YouTubeVideo.published_at)).where(
                YouTubeVideo.canonical_artist_id == artist)).scalar_one_or_none()
        if newest is None:
            return 9999.0  # no registry yet → treat as long-tail cadence
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=UTC)
        return max((now - newest).total_seconds() / 86400.0, 0.0)

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

    def _defer(self, job: DemandRefreshJob, now: datetime, code: str) -> str:
        """Quota reserve reached: put the job back to PENDING for a later pass WITHOUT counting a failure
        or invalidating anything. Attempt count is rolled back so a deferral never burns the retry budget."""
        job.status = "PENDING"
        job.result_code = code
        job.attempt_count = max(job.attempt_count - 1, 0)
        job.scheduled_at = now + timedelta(seconds=settings.demand_scheduler_backoff_base_seconds)
        job.lock_expires_at = None
        job.updated_at = now
        return "DEFERRED"

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
        reattempts = await self.enqueue_identity_reattempts(db, now=now)
        claimed = self.claim(db, worker_id=worker_id, now=now)
        outcomes: dict[str, int] = {}
        for job in claimed:
            code = await self.execute(db, job, now=now)
            outcomes[code] = outcomes.get(code, 0) + 1
            db.flush()
        db.commit()
        return {"enqueued": enq, "identity_reattempts": reattempts,
                "claimed": len(claimed), "outcomes": outcomes}
