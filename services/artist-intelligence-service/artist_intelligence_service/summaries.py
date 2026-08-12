"""Bounded per-artist monitoring summaries (Phase 5B.2 increment 2).

One batch read the gateway can join, keyed by canonical_artist_id, against the authoritative canonical
ARTIST registry — so the Artists list needs a single artist-intelligence call instead of N per-row calls.
Covers only the demand cohort (artists n-quark actually monitors): those with a YouTube identity, tracked
video, demand observation, or watch target. Read-only; movement is computed only for artists that have
owned video content (cheap when there is none).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artist_intelligence_service import movement, watchlist
from artist_intelligence_service.models import ArtistDemandObservation as ADO
from artist_intelligence_service.models import (
    ArtistExternalIdentity,
    ArtistWatchTarget,
    YouTubeVideo,
)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def artist_monitoring_summaries(db: Session) -> dict[str, dict[str, Any]]:
    cohort: set[str] = set()
    cohort |= set(db.execute(select(func.distinct(ArtistExternalIdentity.canonical_artist_id))).scalars())
    cohort |= set(db.execute(select(func.distinct(YouTubeVideo.canonical_artist_id))).scalars())
    cohort |= set(db.execute(select(func.distinct(ADO.canonical_artist_id))).scalars())
    cohort |= set(db.execute(
        select(func.distinct(ArtistWatchTarget.canonical_artist_id)).where(
            ArtistWatchTarget.canonical_artist_id.is_not(None))).scalars())

    # watch-target status per canonical (WATCHING/PAUSED/etc.), computed live.
    watch_status: dict[str, str] = {}
    for t in db.execute(select(ArtistWatchTarget).where(
            ArtistWatchTarget.canonical_artist_id.is_not(None))).scalars():
        watch_status[t.canonical_artist_id] = watchlist.effective_status(db, t)

    # per-artist video + observation aggregates (grouped queries; no per-row round-trips).
    owned: dict[str, int] = {}
    eco: dict[str, int] = {}
    for cid, rel, n in db.execute(
            select(YouTubeVideo.canonical_artist_id, YouTubeVideo.relationship_type, func.count())
            .group_by(YouTubeVideo.canonical_artist_id, YouTubeVideo.relationship_type)).all():
        (owned if rel == "OWNED_CONTENT" else eco)[cid] = (owned if rel == "OWNED_CONTENT" else eco).get(cid, 0) + n
    yt_obs: dict[str, int] = dict(db.execute(
        select(ADO.canonical_artist_id, func.count()).where(ADO.provider == "YOUTUBE")
        .group_by(ADO.canonical_artist_id)).all())
    last_update: dict[str, Any] = dict(db.execute(
        select(ADO.canonical_artist_id, func.max(ADO.observed_at)).group_by(ADO.canonical_artist_id)).all())

    out: dict[str, dict[str, Any]] = {}
    for cid in cohort:
        owned_n = owned.get(cid, 0)
        moving = 0
        if owned_n:
            mv = movement.artist_movement(db, cid)
            moving = len(mv["breakout_candidates"]) + len(mv["rising"])
        out[cid] = {
            "watch_status": watch_status.get(cid),
            "watching": watch_status.get(cid) == watchlist.WATCHING,
            "youtube_identity_state": watchlist.youtube_identity_state(db, cid),  # RESOLVED|AMBIGUOUS|PENDING|None
            "owned_videos": owned_n,
            "ecosystem_videos": eco.get(cid, 0),
            "youtube_observations": yt_obs.get(cid, 0),
            "has_demand_data": bool(yt_obs.get(cid, 0)),
            "moving_content_count": moving,
            "last_demand_update": _iso(last_update.get(cid)),
        }
    return out
