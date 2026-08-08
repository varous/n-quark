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
    BUCKET_SEARCH,
    YT_SEARCH_UNITS,
    can_spend,
    record_meter,
)


def _now() -> datetime:
    return datetime.now(UTC)


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

    created = merged = ran = 0
    stopped_for_quota = False
    for q in queries[:max_q]:
        # reserve/budget-aware: stop before spending the operational reserve.
        if not can_spend(db, PROVIDER_YOUTUBE, BUCKET_SEARCH, YT_SEARCH_UNITS, on=now.date()):
            stopped_for_quota = True
            break
        try:
            results = await provider.search_artist(q, limit=rpq)
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
