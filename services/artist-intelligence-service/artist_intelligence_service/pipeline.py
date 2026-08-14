"""YouTube acquisition pipeline diagnostics (Phase 5B.2.8).

A single read-only view of the identity → owned-content → statistics → movement pipeline, in product
terms, plus **stuck-state** detection (impossible/blocked states) and honest **snapshot semantics**
(metric observations are NOT temporal snapshots). Deterministic; no scores; degrades gracefully.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from artist_intelligence_service.config import settings
from artist_intelligence_service.models import (
    ArtistDemandObservation as ADO,
)
from artist_intelligence_service.models import (
    ArtistExternalIdentity as AEI,
)
from artist_intelligence_service.models import (
    DemandRefreshJob as JOB,
)
from artist_intelligence_service.models import (
    YouTubeVideo as YV,
)
from artist_intelligence_service.providers.base import PROVIDER_YOUTUBE

JOB_IDENTITY = "YOUTUBE_IDENTITY_DISCOVERY"
JOB_CATALOGUE = "YOUTUBE_CATALOGUE_BACKFILL"
JOB_VIDEO = "YOUTUBE_VIDEO_SNAPSHOT"
_ACTIVE = ("PENDING", "RUNNING", "FAILED_RETRYABLE")


def _snapshots_per_video(db: Session) -> dict[str, int]:
    """distinct observation TIMESTAMPS (snapshots) per owned video — not metric-observation rows.
    views/likes/comments recorded at one collection time are one snapshot, three metric observations."""
    rows = db.execute(
        select(ADO.scope_id, func.count(distinct(ADO.observed_at)))
        .where(ADO.scope_type == "CONTENT").group_by(ADO.scope_id)).all()
    return {vid: int(c) for vid, c in rows if vid}


def _next_job_at(db: Session, job_type: str) -> str | None:
    r = db.execute(
        select(func.min(JOB.scheduled_at)).where(
            JOB.job_type == job_type, JOB.status.in_(_ACTIVE))).scalar_one_or_none()
    return r.isoformat() if r else None


def youtube_pipeline(db: Session) -> dict[str, Any]:
    # ---- identity funnel ----
    by_status: dict[str, int] = {}
    for st, n in db.execute(
            select(AEI.status, func.count()).where(
                AEI.provider == PROVIDER_YOUTUBE, AEI.identity_type == "CHANNEL_ID")
            .group_by(AEI.status)).all():
        by_status[st] = n
    verified = by_status.get("RESOLVED", 0)
    ambiguous = by_status.get("AMBIGUOUS", 0)
    unresolved = by_status.get("UNRESOLVED", 0)
    candidates = sum(v for k, v in by_status.items() if k != "REJECTED")
    quota_deferred = int(db.execute(
        select(func.count()).select_from(JOB).where(
            JOB.job_type == JOB_IDENTITY, JOB.status == "PENDING",
            JOB.result_code == "QUOTA_EXHAUSTED")).scalar_one())
    pending_identity = int(db.execute(
        select(func.count()).select_from(JOB).where(
            JOB.job_type == JOB_IDENTITY, JOB.status.in_(_ACTIVE))).scalar_one())

    # ---- owned content ----
    owned_videos = int(db.execute(select(func.count()).select_from(YV)).scalar_one())
    channels_with_catalogue = int(db.execute(
        select(func.count(distinct(YV.channel_id))).select_from(YV)).scalar_one())
    snaps = _snapshots_per_video(db)
    videos_observed = len(snaps)
    ge2 = sum(1 for c in snaps.values() if c >= 2)
    ge3 = sum(1 for c in snaps.values() if c >= settings.movement_min_observations)
    metric_observations = int(db.execute(
        select(func.count()).select_from(ADO).where(ADO.scope_type == "CONTENT")).scalar_one())

    return {
        "eligible_canonical_artists": int(db.execute(
            select(func.count(distinct(AEI.canonical_artist_id))).where(
                AEI.provider == PROVIDER_YOUTUBE)).scalar_one()),
        "identity": {
            "candidates": candidates, "verified_channels": verified,
            "needs_identity_review": ambiguous, "unresolved": unresolved,
            "quota_deferred": quota_deferred, "pending_identity_jobs": pending_identity},
        "owned_content": {
            "verified_channels": verified, "channels_with_catalogue": channels_with_catalogue,
            "owned_videos_tracked": owned_videos, "videos_observed": videos_observed,
            "videos_ge2_snapshots": ge2,
            "videos_with_sufficient_movement_history": ge3},
        # 5B.2.8 §12 — metric-observation count is DISTINCT from temporal-snapshot count
        "snapshot_semantics": {
            "metric_observations": metric_observations,
            "videos_with_1_snapshot": sum(1 for c in snaps.values() if c == 1),
            "videos_with_2plus_snapshots": ge2,
            "videos_with_3plus_snapshots": ge3,
            "movement_min_observations": settings.movement_min_observations,
            "note": "views/likes/comments at one collection time are 3 metric observations = 1 snapshot"},
        "scheduler": {
            "next_identity_job": _next_job_at(db, JOB_IDENTITY),
            "next_catalogue_job": _next_job_at(db, JOB_CATALOGUE),
            "next_stats_job": _next_job_at(db, JOB_VIDEO)},
        "stuck_states": stuck_states(db),
    }


def stuck_states(db: Session) -> dict[str, Any]:
    """Bounded detectors for impossible/blocked acquisition states (5B.2.8 §22). Diagnostics only —
    nothing is auto-repaired (canonical/provider identity is never mutated by a detector)."""
    def _artists_with_job(job_type: str, statuses: tuple[str, ...]) -> set[str]:
        return {r[0] for r in db.execute(
            select(distinct(JOB.canonical_artist_id)).where(
                JOB.job_type == job_type, JOB.status.in_(statuses))).all()}

    resolved_artists = {r[0] for r in db.execute(
        select(distinct(AEI.canonical_artist_id)).where(
            AEI.provider == PROVIDER_YOUTUBE, AEI.identity_type == "CHANNEL_ID",
            AEI.status == "RESOLVED")).all()}
    nonresolved_artists = {r[0] for r in db.execute(
        select(distinct(AEI.canonical_artist_id)).where(
            AEI.provider == PROVIDER_YOUTUBE, AEI.identity_type == "CHANNEL_ID",
            AEI.status.in_(("AMBIGUOUS", "UNRESOLVED"))).where(
            AEI.canonical_artist_id.not_in(resolved_artists) if resolved_artists else True)).all()}
    artists_with_videos = {r[0] for r in db.execute(
        select(distinct(YV.canonical_artist_id))).all()}
    cat_success = _artists_with_job(JOB_CATALOGUE, ("SUCCEEDED",))
    cat_any = _artists_with_job(JOB_CATALOGUE, _ACTIVE + ("SUCCEEDED",))
    vid_any = _artists_with_job(JOB_VIDEO, _ACTIVE + ("SUCCEEDED",))
    vid_success = _artists_with_job(JOB_VIDEO, ("SUCCEEDED",))
    identity_active = _artists_with_job(JOB_IDENTITY, _ACTIVE)

    obs_artists = {r[0] for r in db.execute(
        select(distinct(ADO.canonical_artist_id)).where(ADO.scope_type == "CONTENT")).all()}

    def _s(xs):  # bounded sample for the UI
        return sorted(xs)[:5]

    resolved_without_catalogue = resolved_artists - cat_any
    verified_no_videos_after_catalogue = (cat_success & resolved_artists) - artists_with_videos
    owned_videos_without_stats_job = artists_with_videos - vid_any
    stats_succeeded_no_observations = (vid_success & artists_with_videos) - obs_artists
    nonresolved_without_retry = nonresolved_artists - identity_active

    out = {
        "resolved_without_catalogue_job": {"count": len(resolved_without_catalogue),
                                           "sample": _s(resolved_without_catalogue)},
        "verified_no_videos_after_catalogue": {"count": len(verified_no_videos_after_catalogue),
                                               "sample": _s(verified_no_videos_after_catalogue)},
        "owned_videos_without_stats_job": {"count": len(owned_videos_without_stats_job),
                                           "sample": _s(owned_videos_without_stats_job)},
        "stats_succeeded_no_observations": {"count": len(stats_succeeded_no_observations),
                                            "sample": _s(stats_succeeded_no_observations)},
        "nonresolved_without_retry": {"count": len(nonresolved_without_retry),
                                      "sample": _s(nonresolved_without_retry)},
    }
    out["any_stuck"] = any(v["count"] > 0 for v in out.values())
    return out
