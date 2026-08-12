"""Artist Data Coverage read model (Phase 5B.2) — "what does n-quark actually know about this artist?"

A single, plain-language-ready answer assembled from the systems that already hold the evidence: the
canonical registry (identity), the graph (observed live activity), the YouTube content registry +
observation ledger (content + movement), and the demand ledger (metrics + Trends). It deliberately
distinguishes the states an operator must not confuse:

- ``COLLECTED``            — data exists and is shown;
- ``ZERO_OBSERVED``        — collected, and the true observed value is zero (e.g. 0 events observed);
- ``NOT_COLLECTED``        — n-quark is not collecting this yet (e.g. no verified channel);
- ``UNAVAILABLE``          — a provider/source is unavailable (e.g. Trends official API);
- ``INSUFFICIENT_HISTORY`` — collected, but not enough history to derive a movement value.

Read-only. Computation is deterministic; no scores, no prediction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artist_intelligence_service import movement, reads
from artist_intelligence_service import videos as vids
from artist_intelligence_service.config import settings
from artist_intelligence_service.crawl_client import CrawlServiceClient
from artist_intelligence_service.graph_client import GraphServiceClient
from artist_intelligence_service.models import ArtistDemandObservation as ADO
from artist_intelligence_service.models import ArtistExternalIdentity, YouTubeVideo
from artist_intelligence_service.providers.base import PROVIDER_GOOGLE_TRENDS, PROVIDER_YOUTUBE
from artist_intelligence_service.service import DemandService


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _iso(dt: datetime | None) -> str | None:
    a = _aware(dt)
    return a.isoformat() if a else None


async def artist_data_coverage(db: Session, artist: str, *, crawl: CrawlServiceClient | None = None,
                               graph: GraphServiceClient | None = None,
                               svc: DemandService | None = None,
                               now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    crawl = crawl or CrawlServiceClient()
    graph = graph or GraphServiceClient()
    svc = svc or DemandService()

    # ---- identity ----------------------------------------------------------------------------
    try:
        canonical_backed = await crawl.canonical_artist_registered(artist)
    except Exception:  # noqa: BLE001 — a crawl outage: report unknown, don't fail the page
        canonical_backed = None
    identities = svc.list_identities(db, artist)
    yt_channel = next((i for i in identities
                       if i.provider == PROVIDER_YOUTUBE and i.identity_type == "CHANNEL_ID"
                       and i.status == "RESOLVED"), None)
    yt_pending = [i for i in identities if i.provider == PROVIDER_YOUTUBE and i.status != "RESOLVED"]
    identity = {
        "canonical_artist_id": artist,
        "canonical_status": ("REGISTERED" if canonical_backed else
                             "UNKNOWN" if canonical_backed is None else "NOT_REGISTERED"),
        "youtube_identity": {
            "state": "VERIFIED" if yt_channel else ("PENDING" if yt_pending else "NOT_COLLECTED"),
            "verified_channel_id": yt_channel.provider_id if yt_channel else None,
            "channel_url": yt_channel.canonical_url if yt_channel else None,
            "last_verified_at": _iso(yt_channel.last_verified_at) if yt_channel else None},
        "aliases_note": "canonical aliases are owned by the entity registry (crawl); shown on the full page",
    }

    # ---- live activity (supply, from the graph) ----------------------------------------------
    try:
        from artist_intelligence_service.supply import artist_supply
        sup = await artist_supply(graph, artist, now=now)
        live = {
            "state": "COLLECTED" if sup.get("event_count") else "ZERO_OBSERVED",
            "events_observed": sup.get("event_count", 0),
            "upcoming_events": sup.get("upcoming_events", 0),
            "past_events": max(sup.get("event_count", 0) - sup.get("upcoming_events", 0), 0),
            "cities": sup.get("cities", []),
            "venues_count": len(sup.get("venues", [])),
            "organizers_count": len(sup.get("organizers", [])),
            "last_live_observation": sup.get("last_observed")}
    except Exception:  # noqa: BLE001 — graph outage: degrade honestly
        live = {"state": "UNAVAILABLE", "reason": "graph service unavailable"}

    # ---- youtube content + movement -----------------------------------------------------------
    all_videos = vids.videos_for_artist(db, artist, active_only=False, limit=1000)
    owned = [v for v in all_videos if v.relationship_type == "OWNED_CONTENT"]
    ecosystem = [v for v in all_videos if v.relationship_type == "ECOSYSTEM_CONTENT"]
    recent_discovery = max((_aware(v.first_seen_at) for v in all_videos), default=None)
    last_stat = db.execute(
        select(func.max(ADO.observed_at)).where(ADO.canonical_artist_id == artist,
                                                ADO.scope_type == "CONTENT")).scalar_one_or_none()
    cutoff_24h = datetime.fromtimestamp(now.timestamp() - 24 * 3600, tz=UTC)
    videos_recent = int(db.execute(
        select(func.count(func.distinct(YouTubeVideo.video_id))).where(
            YouTubeVideo.canonical_artist_id == artist,
            YouTubeVideo.last_observed_at >= cutoff_24h)).scalar_one())
    unavailable = sum(1 for v in all_videos if v.availability_state in ("UNAVAILABLE", "NOT_FOUND"))
    if not yt_channel:
        youtube = {"state": "NOT_COLLECTED", "reason": "no verified YouTube channel yet"}
    else:
        mv = movement.artist_movement(db, artist, now=now)
        with_history = sum(1 for r in (mv["breakout_candidates"] + mv["rising"] + mv["cooling"])
                           ) + mv["counts"].get(movement.NORMAL, 0)
        youtube = {
            "state": "COLLECTED",
            "owned_videos_tracked": len(owned),
            "ecosystem_videos_tracked": len(ecosystem),
            "videos_observed_last_24h": videos_recent,
            "videos_with_sufficient_history": with_history,
            "insufficient_history_videos": mv["counts"].get(movement.INSUFFICIENT_HISTORY, 0),
            "most_recent_content_discovery": _iso(recent_discovery),
            "last_statistics_observation": _iso(last_stat),
            "moving_content_count": len(mv["breakout_candidates"]) + len(mv["rising"]),
            "movement_states": mv["counts"],
            "cross_channel_activity": mv["cross_channel_activity"],
            "unavailable_videos": unavailable}

    # ---- demand (metrics + trends + history) --------------------------------------------------
    yt_metric_count = int(db.execute(
        select(func.count()).select_from(ADO).where(
            ADO.canonical_artist_id == artist, ADO.provider == PROVIDER_YOUTUBE)).scalar_one())
    trends_count = int(db.execute(
        select(func.count()).select_from(ADO).where(
            ADO.canonical_artist_id == artist, ADO.provider == PROVIDER_GOOGLE_TRENDS)).scalar_one())
    geo_regions = int(db.execute(
        select(func.count(func.distinct(ADO.scope_id))).where(
            ADO.canonical_artist_id == artist, ADO.scope_type.in_(("REGION", "SUBREGION")))).scalar_one())
    first_obs, last_obs = reads.first_last_observed(db, artist)
    demand = {
        "youtube_metrics": {"state": "COLLECTED" if yt_metric_count else "ZERO_OBSERVED",
                            "observation_count": yt_metric_count},
        "google_trends": {"state": "COLLECTED" if trends_count else "UNAVAILABLE",
                          "observation_count": trends_count,
                          "note": None if trends_count else "Google Trends official API access unavailable "
                                  "on this deployment (import-only)"},
        "geographic_demand": {"state": "COLLECTED" if geo_regions else "ZERO_OBSERVED",
                              "regions_covered": geo_regions},
        "observation_history": {"first_observation": _iso(first_obs), "last_demand_update": _iso(last_obs),
                                "total_observations": reads.observation_count(db, artist)},
    }

    return {
        "canonical_artist_id": artist,
        "generated_at": now.isoformat(),
        "identity": identity,
        "live_activity": live,
        "youtube": youtube,
        "demand": demand,
        "evidence": {
            "first_observation": _iso(first_obs),
            "most_recent_observation": _iso(last_obs),
            "sources_contributing": _sources(db, artist),
            "freshness_stale_after_hours": settings.demand_freshness_stale_hours},
        "disclaimer": "Observed coverage for a bounded set of tracked signals — not complete market "
                      "coverage. Absence of data is shown as 'not collected' / 'unavailable', never as zero.",
    }


def _sources(db: Session, artist: str) -> list[str]:
    return sorted(db.execute(
        select(func.distinct(ADO.provider)).where(ADO.canonical_artist_id == artist)).scalars())
