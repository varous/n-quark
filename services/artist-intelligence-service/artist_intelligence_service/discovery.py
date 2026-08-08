"""Independent YouTube artist discovery (Phase 5A.3).

YouTube becomes an artist DISCOVERY surface in addition to an identity provider: bounded, configurable,
India-market-oriented official Data API searches produce ``artist_candidate`` rows — NEVER canonical
artists. Discovery is quota-guarded (SEARCH bucket + global reserve) so it degrades gracefully, and the
query set is version-controlled config, not a hardcoded list buried in business logic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from artist_intelligence_service import candidates as cand
from artist_intelligence_service.config import settings
from artist_intelligence_service.providers.base import PROVIDER_YOUTUBE
from artist_intelligence_service.providers.youtube import YouTubeProvider
from artist_intelligence_service.quota import (
    BUCKET_GENERAL_READ,
    SEARCH_DISCOVERY,
    YT_SEARCH_UNITS,
    can_spend,
    can_spend_search,
    near_provider_reset,
    record_meter,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _unresolved_identity_backlog(db: Session) -> bool:
    """Whether any identity-discovery job is still pending — gates discovery's borrowing of unused
    unresolved allocation (discovery yields to the known-artist backlog first)."""
    from sqlalchemy import select

    from artist_intelligence_service.models import DemandRefreshJob
    return db.execute(
        select(DemandRefreshJob.id).where(
            DemandRefreshJob.job_type == "YOUTUBE_IDENTITY_DISCOVERY",
            DemandRefreshJob.status.in_(("PENDING", "FAILED_RETRYABLE", "RUNNING"))).limit(1)
    ).scalar_one_or_none() is not None


async def run_youtube_discovery(db: Session, *, provider: YouTubeProvider | None = None,
                                max_queries: int | None = None, results_per_query: int | None = None,
                                now: datetime | None = None) -> dict[str, Any]:
    """Run bounded India-market discovery queries → artist candidates (never canonical artists)."""
    now = now or _now()
    if not settings.youtube_discovery_enabled:
        return {"status": "DISABLED", "queries_run": 0, "candidates_created": 0, "candidates_merged": 0}
    provider = provider or YouTubeProvider()
    queries = settings.discovery_queries
    max_q = max_queries if max_queries is not None else settings.youtube_discovery_per_run
    rpq = results_per_query or settings.youtube_discovery_results_per_query

    # discovery may borrow unused SEARCH allocation only when the unresolved backlog is empty.
    unresolved_backlog = _unresolved_identity_backlog(db)
    near_reset = near_provider_reset(now=now)
    created = merged = ran = 0
    stopped_for_quota = False
    for q in queries[:max_q]:
        # per-purpose allocation + global reserve; stops before spending the reserve.
        if not can_spend_search(db, PROVIDER_YOUTUBE, SEARCH_DISCOVERY, YT_SEARCH_UNITS,
                                others_have_backlog=unresolved_backlog, near_reset=near_reset,
                                on=now.date()):
            stopped_for_quota = True
            break
        try:
            results = await provider.search_artist(q, limit=rpq, purpose=SEARCH_DISCOVERY)
        except Exception:  # noqa: BLE001 — a bad query never stops discovery; account the failed search
            record_meter(db, PROVIDER_YOUTUBE, provider.meter)
            continue
        record_meter(db, PROVIDER_YOUTUBE, provider.meter)
        ran += 1
        for c in results:
            if not c.provider_id:
                continue
            _, is_new = cand.upsert_candidate(
                db, display_name=c.display_name, discovery_source=cand.SRC_YOUTUBE_SEARCH,
                discovery_source_id=c.provider_id, discovery_method=f"search:{q}",
                source_url=c.canonical_url, hints={"country": "IN"},
                evidence={"title": c.display_name, "query": q, **(c.evidence or {})},
                provenance={"provider": PROVIDER_YOUTUBE, "discovery_query": q,
                            "acquisition_method": "signal_service"}, now=now)
            created += 1 if is_new else 0
            merged += 0 if is_new else 1
    return {"status": "OK" if not stopped_for_quota else "QUOTA_LIMITED",
            "queries_run": ran, "candidates_created": created, "candidates_merged": merged}


async def run_ecosystem_discovery(db: Session, *, provider: YouTubeProvider | None = None,
                                  now: datetime | None = None) -> dict[str, Any]:
    """Bounded YouTube ECOSYSTEM discovery (Phase 5A.3.1): configured seed channels (festival / promoter /
    venue / music-media / label) → their recent uploads (official API) → `artist_candidate` evidence.

    Deliberately conservative: it fans out ONE hop from named seeds (no recursive crawling of arbitrary
    related channels), is bounded by config (seeds/run, videos/seed, candidates/run), and produces
    candidates only — never canonical artists. Ecosystem candidates are weak by construction and only
    canonicalise later if the promotion policy is independently satisfied."""
    now = now or _now()
    if not settings.youtube_ecosystem_enabled:
        return {"status": "DISABLED", "seeds_run": 0, "candidates_created": 0}
    seeds = settings.ecosystem_seed_channels
    if not seeds:
        return {"status": "NO_SEEDS", "seeds_run": 0, "candidates_created": 0}
    provider = provider or YouTubeProvider()
    max_seeds = settings.youtube_ecosystem_max_seeds_per_run
    per_seed = settings.youtube_ecosystem_max_videos_per_seed
    cap = settings.youtube_ecosystem_max_candidates_per_run

    created = merged = seeds_run = 0
    for seed in seeds[:max_seeds]:
        if created >= cap:
            break
        # uploads read ≈ 3 general-read units; stop before the reserve.
        if not can_spend(db, PROVIDER_YOUTUBE, BUCKET_GENERAL_READ, 3, on=now.date()):
            break
        try:
            uploads = await provider.list_uploads(seed, limit=per_seed)
        except Exception:  # noqa: BLE001 — a bad seed never stops ecosystem discovery
            record_meter(db, PROVIDER_YOUTUBE, provider.meter)
            continue
        record_meter(db, PROVIDER_YOUTUBE, provider.meter)
        seeds_run += 1
        for v in uploads.get("videos") or []:
            if created >= cap:
                break
            vid = v.get("video_id")
            title = v.get("title")
            if not vid or not title:
                continue
            _, is_new = cand.upsert_candidate(
                db, display_name=title, discovery_source=cand.SRC_YOUTUBE_ECOSYSTEM,
                discovery_source_id=vid, discovery_method=f"ecosystem_upload:{seed}",
                hints={"country": "IN"},
                evidence={"seed_channel": seed, "video_title": title,
                          "published_at": v.get("published_at"), "kind": "ecosystem_upload_title"},
                provenance={"provider": PROVIDER_YOUTUBE, "seed_channel": seed,
                            "acquisition_method": "signal_service"}, now=now)
            created += 1 if is_new else 0
            merged += 0 if is_new else 1
    return {"status": "OK", "seeds_run": seeds_run, "candidates_created": created,
            "candidates_merged": merged}
