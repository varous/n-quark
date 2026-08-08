"""YouTube video registry helpers (Phase 5A.3).

The registry (``youtube_video``) holds stable, mostly-static per-video metadata captured once. Demand
metrics over time (views/likes/comments) live separately in ``artist_demand_observation`` keyed by hour.
This separation lets a channel's whole catalogue be tracked without re-fetching static metadata, and
supports longitudinal analysis beyond a "latest five videos" window.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artist_intelligence_service.models import YouTubeVideo


def _now() -> datetime:
    return datetime.now(UTC)


def upsert_video(db: Session, *, video_id: str, channel_id: str, canonical_artist_id: str,
                 external_identity_id: str | None = None, title: str | None = None,
                 published_at: datetime | None = None, duration_seconds: int | None = None,
                 category: str | None = None, live_status: str | None = None,
                 metadata: dict[str, Any] | None = None,
                 now: datetime | None = None) -> tuple[YouTubeVideo, bool]:
    """Idempotent on video_id. Captures stable metadata once; refreshes last_observed_at + fills gaps."""
    now = now or _now()
    row = db.execute(select(YouTubeVideo).where(YouTubeVideo.video_id == video_id)).scalar_one_or_none()
    if row is not None:
        # stable metadata is captured once; only fill missing fields, never rewrite settled history.
        row.last_observed_at = now
        if title and not row.title:
            row.title = title
        if published_at and not row.published_at:
            row.published_at = published_at
        if duration_seconds is not None and row.duration_seconds is None:
            row.duration_seconds = duration_seconds
        if category and not row.category:
            row.category = category
        if live_status and not row.live_status:
            row.live_status = live_status
        row.updated_at = now
        db.flush()
        return row, False
    row = YouTubeVideo(
        video_id=video_id, channel_id=channel_id, canonical_artist_id=canonical_artist_id,
        external_identity_id=external_identity_id, title=title, published_at=published_at,
        duration_seconds=duration_seconds, category=category, live_status=live_status,
        tracking_status="ACTIVE", first_seen_at=now, last_observed_at=now,
        metadata_json=metadata or {}, created_at=now, updated_at=now)
    db.add(row)
    db.flush()
    return row, True


def videos_for_artist(db: Session, canonical_artist_id: str, *, active_only: bool = True,
                      limit: int = 500) -> list[YouTubeVideo]:
    q = select(YouTubeVideo).where(YouTubeVideo.canonical_artist_id == canonical_artist_id)
    if active_only:
        q = q.where(YouTubeVideo.tracking_status == "ACTIVE")
    return list(db.execute(q.order_by(YouTubeVideo.published_at.desc().nullslast()).limit(limit)).scalars())


def video_counts(db: Session) -> dict[str, int]:
    total = int(db.execute(select(func.count()).select_from(YouTubeVideo)).scalar_one())
    active = int(db.execute(
        select(func.count()).select_from(YouTubeVideo)
        .where(YouTubeVideo.tracking_status == "ACTIVE")).scalar_one())
    return {"videos_discovered": total, "videos_active": active}
