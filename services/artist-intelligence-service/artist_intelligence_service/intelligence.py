"""Read-model orchestration (Phase 5A) — assembles the API responses from DB reads + pure read models
+ observed supply. Descriptive only: independent measures, transparent labels, no combined score, no
causal claim. Demand and supply are juxtaposed through ``canonical_artist_id``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artist_intelligence_service import readmodels, reads
from artist_intelligence_service.config import settings
from artist_intelligence_service.crawl_client import CrawlServiceClient
from artist_intelligence_service.graph_client import GraphServiceClient
from artist_intelligence_service.models import ArtistDemandObservation as ADO
from artist_intelligence_service.models import (
    ArtistExternalIdentity,
    DemandRefreshJob,
    ProviderQuotaDay,
)
from artist_intelligence_service.providers.base import (
    AMBIGUOUS,
    GOOGLE_SEARCH_INTEREST,
    PROVIDER_GOOGLE_TRENDS,
    PROVIDER_YOUTUBE,
    RESOLVED,
    UNRESOLVED,
    YT_CHANNEL_VIEWS,
    YT_SUBSCRIBERS,
    YT_VIDEO_COMMENTS,
    YT_VIDEO_LIKES,
    YT_VIDEO_VIEWS,
)
from artist_intelligence_service.service import DemandService
from artist_intelligence_service.supply import artist_supply, region_slug


def _now() -> datetime:
    return datetime.now(UTC)


def _identity_dict(i: ArtistExternalIdentity) -> dict[str, Any]:
    return {"id": i.id, "provider": i.provider, "identity_type": i.identity_type,
            "provider_id": i.provider_id, "display_name": i.display_name, "status": i.status,
            "confidence": i.confidence, "resolution_method": i.resolution_method,
            "canonical_url": i.canonical_url, "first_seen_at": i.first_seen_at.isoformat(),
            "last_verified_at": i.last_verified_at.isoformat() if i.last_verified_at else None}


# ---- momentum ---------------------------------------------------------------------------------
def build_momentum(db: Session, artist: str, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    views = reads.series(db, artist, PROVIDER_YOUTUBE, YT_CHANNEL_VIEWS)
    subs = reads.series(db, artist, PROVIDER_YOUTUBE, YT_SUBSCRIBERS)
    video_views = reads.content_series(db, artist, YT_VIDEO_VIEWS)
    trends_country = reads.series(db, artist, PROVIDER_GOOGLE_TRENDS, GOOGLE_SEARCH_INTEREST,
                                  scope_type="COUNTRY")
    pubs = reads.video_published_dates(db, artist)

    # engagement ratio on the newest tracked video
    likes = {r["scope_id"]: r for r in reads.latest_by_scope(db, artist, PROVIDER_YOUTUBE, YT_VIDEO_LIKES, "CONTENT")}
    comments = {r["scope_id"]: r for r in reads.latest_by_scope(db, artist, PROVIDER_YOUTUBE, YT_VIDEO_COMMENTS, "CONTENT")}
    view_rows = reads.latest_by_scope(db, artist, PROVIDER_YOUTUBE, YT_VIDEO_VIEWS, "CONTENT")
    newest = max(view_rows, key=lambda r: (r.get("provider_timestamp") or now), default=None)
    if newest:
        eng = readmodels.engagement_ratio(
            newest.get("value"), (likes.get(newest["scope_id"]) or {}).get("value"),
            (comments.get(newest["scope_id"]) or {}).get("value"))
    else:
        eng = {"status": readmodels.INSUFFICIENT, "reason": "no_tracked_videos"}

    first_obs, last_obs = reads.first_last_observed(db, artist)
    return {
        "canonical_artist_id": artist,
        "components": {
            "youtube_channel_view_velocity": {
                "delta_7d": readmodels.window_delta(views, now, 7),
                "delta_30d": readmodels.window_delta(views, now, 30)},
            "youtube_subscriber_change": {
                "delta_7d": readmodels.window_delta(subs, now, 7),
                "delta_30d": readmodels.window_delta(subs, now, 30)},
            "youtube_recent_video_velocity": readmodels.content_velocity(video_views, now),
            "youtube_upload_activity": readmodels.upload_activity(pubs, now),
            "youtube_recent_video_engagement_ratio": eng,
            "google_search_interest_change": {
                "delta_7d": readmodels.window_delta(trends_country, now, 7),
                "delta_30d": readmodels.window_delta(trends_country, now, 30)},
        },
        "coverage": {
            "observation_count": reads.observation_count(db, artist),
            "first_observed_at": first_obs.isoformat() if first_obs else None,
            "last_observed_at": last_obs.isoformat() if last_obs else None,
            "freshness": readmodels.freshness(last_obs, now),
            "has_7d_history": _has_history_days(db, artist, now, 7),
            "has_30d_history": _has_history_days(db, artist, now, 30),
        },
        "notes": ["components are independent measures; not combined into any score",
                  "not popularity / market value / ticket demand / booking potential"],
    }


def _has_history_days(db: Session, artist: str, now: datetime, days: int) -> bool:
    from datetime import timedelta
    cutoff = now - timedelta(days=days)
    oldest = db.execute(
        select(func.min(ADO.observed_at)).where(ADO.canonical_artist_id == artist)
    ).scalar_one_or_none()
    return oldest is not None and reads._aware(oldest) <= cutoff


# ---- geography --------------------------------------------------------------------------------
async def build_geography(db: Session, artist: str, *, graph: GraphServiceClient | None = None,
                          now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    graph = graph or GraphServiceClient()
    regions = reads.latest_by_scope(db, artist, PROVIDER_GOOGLE_TRENDS, GOOGLE_SEARCH_INTEREST, "REGION")
    supply = await artist_supply(graph, artist, now=now)
    supply_regions = supply.get("regions", {})

    combined: dict[str, dict[str, Any]] = {}
    for r in regions:
        # join on the region's human label (aligns Trends 'West Bengal' with graph 'region:west-bengal')
        slug = region_slug(r.get("scope_label") or r["scope_id"] or "")
        s = supply_regions.get(slug, {})
        interest = r.get("value")
        combined[slug] = {
            "region_slug": slug, "region_scope_id": r["scope_id"], "region_label": r["scope_label"],
            "search_interest": interest, "evidence_status": r.get("evidence_status"),
            "normalization_context": {k: r.get("provenance", {}).get(k) for k in
                                      ("normalization", "comparison_window", "time_range", "geo")},
            "observed_supply_count": s.get("event_count", 0),
            "recent_live_activity": s.get("recent", 0), "upcoming_live_activity": s.get("upcoming", 0),
            "label": readmodels.geo_affinity_label(interest, s.get("event_count", 0)),
        }
    # regions with supply but no demand data (still informative)
    for slug, s in supply_regions.items():
        if slug not in combined:
            combined[slug] = {
                "region_slug": slug, "region_scope_id": s.get("region_id"), "region_label": None,
                "search_interest": None, "evidence_status": None, "normalization_context": {},
                "observed_supply_count": s.get("event_count", 0),
                "recent_live_activity": s.get("recent", 0), "upcoming_live_activity": s.get("upcoming", 0),
                "label": readmodels.geo_affinity_label(None, s.get("event_count", 0))}
    return {"canonical_artist_id": artist,
            "regions": sorted(combined.values(),
                              key=lambda x: (x["search_interest"] or -1), reverse=True),
            "notes": ["labels are relative to this artist's analysed cohort; thresholds are configurable",
                      "provider geography granularity is preserved; no city precision is invented",
                      "0-100 interest is relative within a pull; not comparable across pulls"]}


# ---- demand (identities + youtube + trends + supply) ------------------------------------------
async def build_demand(db: Session, artist: str, *, graph: GraphServiceClient | None = None,
                       now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    graph = graph or GraphServiceClient()
    identities = [_identity_dict(i) for i in DemandService().list_identities(db, artist)]

    views = reads.series(db, artist, PROVIDER_YOUTUBE, YT_CHANNEL_VIEWS)
    subs = reads.series(db, artist, PROVIDER_YOUTUBE, YT_SUBSCRIBERS)
    vids = reads.series(db, artist, PROVIDER_YOUTUBE, "YOUTUBE_VIDEO_COUNT")
    _, yt_last = _provider_first_last(db, artist, PROVIDER_YOUTUBE)
    youtube = {
        "current_snapshot": {
            "channel_views": readmodels.latest_value(views),
            "subscribers": readmodels.latest_value(subs),
            "video_count": readmodels.latest_value(vids)},
        "deltas": {
            "channel_view_delta_7d": readmodels.window_delta(views, now, 7),
            "channel_view_delta_30d": readmodels.window_delta(views, now, 30),
            "subscriber_delta_7d": readmodels.window_delta(subs, now, 7),
            "subscriber_delta_30d": readmodels.window_delta(subs, now, 30)},
        "recent_video_activity": readmodels.content_velocity(
            reads.content_series(db, artist, YT_VIDEO_VIEWS), now),
        "data_freshness": readmodels.freshness(yt_last, now),
    }

    country = reads.series(db, artist, PROVIDER_GOOGLE_TRENDS, GOOGLE_SEARCH_INTEREST, scope_type="COUNTRY")
    regions = reads.latest_by_scope(db, artist, PROVIDER_GOOGLE_TRENDS, GOOGLE_SEARCH_INTEREST, "REGION")
    _, tr_last = _provider_first_last(db, artist, PROVIDER_GOOGLE_TRENDS)
    norm_ctx = regions[0]["provenance"] if regions else (
        {"note": "no trends observations"})
    trends = {
        "current_interest": readmodels.latest_value(country),
        "historical_interest_points": len(country),
        "regional_distribution": [{"region": r["scope_label"], "scope_id": r["scope_id"],
                                   "interest": r["value"]} for r in
                                  sorted(regions, key=lambda x: (x["value"] or -1), reverse=True)],
        "data_freshness": readmodels.freshness(tr_last, now),
        "normalization_context": {k: norm_ctx.get(k) for k in
                                  ("normalization", "comparison_window", "time_range", "geo",
                                   "identity_basis", "provider_mode")},
    }

    supply = await artist_supply(graph, artist, now=now)
    supply_view = {k: supply[k] for k in ("event_count", "upcoming_events", "recent_events", "cities",
                                          "venues", "organizers", "first_observed", "last_observed")}
    supply_view["regions"] = list(supply.get("regions", {}).keys())

    return {"canonical_artist_id": artist, "external_identities": identities,
            "youtube": youtube, "google_trends": trends, "observed_live_supply": supply_view,
            "notes": ["demand and supply are juxtaposed via canonical_artist_id; no combined score"]}


def _provider_first_last(db: Session, artist: str, provider: str):
    row = db.execute(
        select(func.min(ADO.observed_at), func.max(ADO.observed_at))
        .where(ADO.canonical_artist_id == artist, ADO.provider == provider)
    ).one()
    return reads._aware(row[0]), reads._aware(row[1])


# ---- event-response ---------------------------------------------------------------------------
async def build_event_response(db: Session, artist: str, event_id: str, *,
                               graph: GraphServiceClient | None = None,
                               now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    graph = graph or GraphServiceClient()
    node = await graph.get_node(event_id)
    if node is None:
        return {"status": "EVENT_NOT_FOUND", "event_id": event_id}
    props = (node.get("properties") or {})
    starts_raw = props.get("starts_at") or props.get("start_time")
    from artist_intelligence_service.supply import _parse_dt
    starts_at = _parse_dt(starts_raw)
    if starts_at is None:
        return {"status": "INSUFFICIENT_HISTORY", "reason": "event_has_no_start_date",
                "event_id": event_id}

    trends_country = reads.series(db, artist, PROVIDER_GOOGLE_TRENDS, GOOGLE_SEARCH_INTEREST,
                                  scope_type="COUNTRY")
    channel_views = reads.series(db, artist, PROVIDER_YOUTUBE, YT_CHANNEL_VIEWS)
    ledger = await _shadow_ledger_transitions(graph, event_id)
    timeline = readmodels.event_response_timeline(
        starts_at=starts_at, trends_country=trends_country, channel_views=channel_views,
        ledger_transitions=ledger, now=now)
    return {"canonical_artist_id": artist, "event_id": event_id, **timeline}


async def _shadow_ledger_transitions(graph: GraphServiceClient, event_id: str) -> list[dict[str, Any]]:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
            resp = await client.get(
                f"{graph.base_url}/v1/internal/events/{event_id}/shadow-ledger")
            if resp.status_code != 200:
                return []
            body = resp.json()
    except httpx.HTTPError:
        return []
    out = []
    for t in (body.get("transitions") or body.get("history") or []):
        out.append({"observed_at": t.get("observed_at") or t.get("captured_at"),
                    "kind": t.get("kind") or t.get("transition_type") or t.get("change"),
                    "detail": {k: t.get(k) for k in ("from", "to", "field") if k in t}})
    return out


# ---- diagnostics ------------------------------------------------------------------------------
async def build_coverage(db: Session, *, crawl: CrawlServiceClient | None = None) -> dict[str, Any]:
    crawl = crawl or CrawlServiceClient()
    now = _now()
    try:
        canonical_artists = len(await crawl.artists(limit=200))
    except Exception:  # noqa: BLE001 — coverage must not fail if crawl is unreachable
        canonical_artists = None

    def _count(*conds) -> int:
        return int(db.execute(select(func.count()).select_from(ADO).where(*conds)).scalar_one()) \
            if conds else 0

    yt_identities = db.execute(
        select(ArtistExternalIdentity.status, func.count())
        .where(ArtistExternalIdentity.provider == PROVIDER_YOUTUBE,
               ArtistExternalIdentity.identity_type == "CHANNEL_ID")
        .group_by(ArtistExternalIdentity.status)).all()
    yt_by_status = {s: c for s, c in yt_identities}

    artists_with_obs = db.execute(
        select(func.count(func.distinct(ADO.canonical_artist_id)))).scalar_one()
    trends_mappings = db.execute(
        select(func.count()).select_from(ArtistExternalIdentity)
        .where(ArtistExternalIdentity.provider == PROVIDER_GOOGLE_TRENDS)).scalar_one()
    trends_obs = db.execute(
        select(func.count()).select_from(ADO).where(ADO.provider == PROVIDER_GOOGLE_TRENDS)).scalar_one()
    imported = db.execute(
        select(func.count()).select_from(ADO)
        .where(ADO.provider == PROVIDER_GOOGLE_TRENDS,
               ADO.evidence_status == "IMPORTED_PROVIDER_EXPORT")).scalar_one()
    regions_covered = db.execute(
        select(func.count(func.distinct(ADO.scope_id)))
        .where(ADO.scope_type == "REGION")).scalar_one()
    from datetime import timedelta
    stale = db.execute(
        select(func.count(func.distinct(ADO.canonical_artist_id)))
        .where(ADO.observed_at < now - timedelta(hours=settings.demand_freshness_stale_hours))
    ).scalar_one()

    quota_today = _quota_today(db)
    failed_jobs = db.execute(
        select(func.count()).select_from(DemandRefreshJob)
        .where(DemandRefreshJob.status == "FAILED_TERMINAL")).scalar_one()

    return {
        "canonical_artists": canonical_artists,
        "youtube_identity_status": {
            "resolved": yt_by_status.get(RESOLVED, 0),
            "ambiguous": yt_by_status.get(AMBIGUOUS, 0),
            "unresolved": yt_by_status.get(UNRESOLVED, 0),
            "artists_with_youtube_identity": sum(yt_by_status.values())},
        "artists_with_demand_observation": artists_with_obs,
        "trends_mappings": trends_mappings, "trends_observations": trends_obs,
        "trends_api_vs_imported": {"imported_provider_export": imported,
                                   "official_api": trends_obs - imported},
        "regions_covered": regions_covered,
        "stale_demand_artists": stale, "provider_failures_terminal_jobs": failed_jobs,
        "youtube_quota_today": quota_today,
        "disclaimer": "observed public demand for a bounded cohort; NOT complete market demand coverage",
    }


def _quota_today(db: Session) -> dict[str, Any]:
    row = db.execute(
        select(ProviderQuotaDay).where(ProviderQuotaDay.provider == PROVIDER_YOUTUBE,
                                       ProviderQuotaDay.quota_date == _now().date())
    ).scalar_one_or_none()
    if row is None:
        return {"requests": 0, "search_requests": 0, "search_quota_units": 0,
                "non_search_quota_units": 0, "quota_errors": 0}
    return {"requests": row.requests, "search_requests": row.search_requests,
            "search_quota_units": row.search_quota_units,
            "non_search_quota_units": row.non_search_quota_units,
            "successful_calls": row.successful_calls, "failed_calls": row.failed_calls,
            "quota_errors": row.quota_errors}


def build_quota(db: Session) -> dict[str, Any]:
    rows = db.execute(select(ProviderQuotaDay).order_by(ProviderQuotaDay.quota_date.desc())).scalars()
    return {"days": [{"provider": r.provider, "date": r.quota_date.isoformat(),
                      "requests": r.requests, "search_requests": r.search_requests,
                      "search_quota_units": r.search_quota_units,
                      "non_search_quota_units": r.non_search_quota_units,
                      "successful_calls": r.successful_calls, "failed_calls": r.failed_calls,
                      "quota_errors": r.quota_errors} for r in rows],
            "youtube_max_searches_per_day": settings.youtube_max_searches_per_day}


def build_provider_health(db: Session) -> dict[str, Any]:
    svc = DemandService()
    return {
        "providers": {
            "youtube": {"enabled": settings.youtube_enabled,
                        "search_enabled": settings.youtube_search_enabled,
                        "acquisition": "signal-service", "quota_today": _quota_today(db)},
            "google_trends": svc.trends_official_status() | {
                "import_enabled": settings.resolved_trends_mode in ("IMPORT", "OFFICIAL_API")},
        },
    }
