"""Artist-universe onboarding, backfill, and coverage diagnostics (Phase 5A.3).

Decouples the demand universe from ticketing coverage:
- ``onboard_artist`` — an event-derived (or otherwise evidenced) canonical artist becomes eligible for
  demand identity resolution automatically: records a RESOLVED candidate + India market evidence and
  enqueues an identity-discovery job if no RESOLVED YouTube identity exists yet. No manual operator call.
- ``backfill_missing_identities`` — enumerates the existing canonical ARTIST cohort and queues those
  without a YouTube identity, as persisted work (never a synchronous mass resolve, never bypassing quota).

Canonical artists are owned by the entity/graph architecture; this module only proposes and links.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artist_intelligence_service import candidates as cand
from artist_intelligence_service import cadence as cad
from artist_intelligence_service.crawl_client import CrawlServiceClient
from artist_intelligence_service.models import (
    ArtistCandidate,
    ArtistExternalIdentity,
    ArtistMarketEvidence,
)
from artist_intelligence_service.providers.base import (
    INDIA_CONFIRMED_LIVE,
    PROVIDER_YOUTUBE,
    RESOLVED,
)
from artist_intelligence_service.scheduler import DemandScheduler


def _now() -> datetime:
    return datetime.now(UTC)


def _has_resolved_youtube(db: Session, canonical_artist_id: str) -> bool:
    return db.execute(
        select(ArtistExternalIdentity.id).where(
            ArtistExternalIdentity.canonical_artist_id == canonical_artist_id,
            ArtistExternalIdentity.provider == PROVIDER_YOUTUBE,
            ArtistExternalIdentity.identity_type == "CHANNEL_ID",
            ArtistExternalIdentity.status == RESOLVED).limit(1)
    ).scalar_one_or_none() is not None


def onboard_artist(db: Session, *, canonical_artist_id: str, display_name: str,
                   discovery_source: str = cand.SRC_EVENT, source_id: str | None = None,
                   evidence_class: str | None = INDIA_CONFIRMED_LIVE, evidence_source: str = "EVENT",
                   evidence_ref: str | None = None, priority: int = cad.P2_CONFIRMED_OR_STRONG,
                   scheduler: DemandScheduler | None = None,
                   now: datetime | None = None) -> dict[str, Any]:
    """Onboard a canonical artist into the demand pipeline (idempotent). Records candidate + evidence and
    enqueues identity discovery if there's no RESOLVED YouTube identity yet."""
    now = now or _now()
    scheduler = scheduler or DemandScheduler()
    source_id = source_id or canonical_artist_id

    cand.upsert_candidate(
        db, display_name=display_name, discovery_source=discovery_source, discovery_source_id=source_id,
        discovery_method="event_derived" if discovery_source == cand.SRC_EVENT else "onboard",
        canonical_artist_id=canonical_artist_id, status=cand.RESOLVED,
        evidence={"canonical_artist_id": canonical_artist_id},
        provenance={"onboarded_at": now.isoformat(), "source": discovery_source}, now=now)

    evidence_recorded = False
    if evidence_class:
        _, evidence_recorded = cand.record_market_evidence(
            db, canonical_artist_id=canonical_artist_id, evidence_class=evidence_class,
            source=evidence_source, source_ref=evidence_ref or source_id,
            provenance={"onboarded_at": now.isoformat()}, now=now)

    queued = False
    already = _has_resolved_youtube(db, canonical_artist_id)
    if not already:
        queued = scheduler.enqueue_identity_discovery(
            db, canonical_artist_id, display_name=display_name, priority=priority, now=now)
    return {"canonical_artist_id": canonical_artist_id, "identity_present": already,
            "identity_discovery_queued": queued, "market_evidence_recorded": evidence_recorded}


async def backfill_missing_identities(db: Session, *, crawl: CrawlServiceClient | None = None,
                                      scheduler: DemandScheduler | None = None,
                                      limit: int | None = None,
                                      now: datetime | None = None) -> dict[str, Any]:
    """Enumerate canonical ARTIST entities and enqueue those lacking a YouTube identity (persisted work).

    Bounded per call (``artist_backfill_batch_size``) so a large cohort is drained across passes, never
    resolved synchronously in one request and never bypassing quota."""
    from artist_intelligence_service.config import settings
    now = now or _now()
    crawl = crawl or CrawlServiceClient()
    scheduler = scheduler or DemandScheduler()
    limit = limit or settings.artist_backfill_batch_size
    try:
        artists = await crawl.artists(limit=200)
    except Exception:  # noqa: BLE001 — a crawl outage must not fail backfill; report zero this pass
        return {"status": "CRAWL_UNAVAILABLE", "examined": 0, "queued": 0}
    queued = examined = 0
    for a in artists:
        cid = a.get("canonical_entity_id") or a.get("canonical_artist_id") or a.get("id")
        name = a.get("canonical_name") or a.get("display_name") or cid
        if not cid:
            continue
        examined += 1
        if _has_resolved_youtube(db, cid):
            continue
        if scheduler.enqueue_identity_discovery(db, cid, display_name=str(name),
                                                priority=cad.P3_YT_DISCOVERY, now=now):
            queued += 1
        if queued >= limit:
            break
    db.flush()
    return {"status": "OK", "examined": examined, "queued": queued, "batch_limit": limit}


# ---- coverage diagnostics ----------------------------------------------------------------------
async def build_artist_universe(db: Session, *, crawl: CrawlServiceClient | None = None) -> dict[str, Any]:
    """Deterministic artist-universe coverage metrics (no proprietary scoring)."""
    from artist_intelligence_service import videos
    from artist_intelligence_service.intelligence import build_coverage

    coverage = await build_coverage(db, crawl=crawl)
    candidate_counts = cand.candidate_counts(db)

    # India market-presence evidence by class (distinct artists per class).
    ev_rows = db.execute(
        select(ArtistMarketEvidence.evidence_class,
               func.count(func.distinct(ArtistMarketEvidence.canonical_artist_id)))
        .group_by(ArtistMarketEvidence.evidence_class)).all()
    india = {c: n for c, n in ev_rows}

    # discovery-source contribution + single- vs multi-source artists.
    src_rows = db.execute(
        select(ArtistCandidate.discovery_source, func.count())
        .group_by(ArtistCandidate.discovery_source)).all()
    by_source = {s: n for s, n in src_rows}
    resolved_multi = db.execute(
        select(func.count()).select_from(
            select(ArtistCandidate.canonical_artist_id)
            .where(ArtistCandidate.canonical_artist_id.is_not(None))
            .group_by(ArtistCandidate.canonical_artist_id)
            .having(func.count(func.distinct(ArtistCandidate.discovery_source)) > 1).subquery())
    ).scalar_one()

    from artist_intelligence_service.scheduler import JOB_IDENTITY
    identity_queue_depth = int(db.execute(
        select(func.count()).select_from(_JOB).where(
            _JOB.job_type == JOB_IDENTITY,
            _JOB.status.in_(("PENDING", "FAILED_RETRYABLE", "RUNNING")))).scalar_one())

    reconciliation = await _reconciliation_diagnostics(db, crawl=crawl)

    return {
        "candidates": candidate_counts,
        "india_market_presence": {
            "confirmed_live_india": india.get("CONFIRMED_LIVE_INDIA", 0),
            "india_demand_observed": india.get("INDIA_DEMAND_OBSERVED", 0),
            "india_market_candidate": india.get("INDIA_MARKET_CANDIDATE", 0)},
        "youtube_identity": coverage.get("youtube_identity_status", {}),
        "artists_with_demand_observation": coverage.get("artists_with_demand_observation"),
        "videos": videos.video_counts(db),
        "discovery_contribution_by_source": by_source,
        "multi_source_artists": int(resolved_multi),
        "identity_resolution_queue_depth": identity_queue_depth,
        "canonical_reconciliation": reconciliation,
        "disclaimer": coverage.get("disclaimer"),
    }


async def _reconciliation_diagnostics(db: Session, *, crawl: CrawlServiceClient | None = None) -> dict[str, Any]:
    """Phase 5A.3.2 — make canonical-artist divergence visible (read-only, bounded).

    Compares the authoritative canonical ARTIST enumeration (crawl entity-resolution registry) against
    the graph ARTIST-node representation and the artist ids referenced by the demand ledger, and reports
    orphan demand references (canonical_artist_id values not present in the canonical enumeration)."""
    from artist_intelligence_service.graph_client import GraphServiceClient
    crawl = crawl or CrawlServiceClient()

    # authoritative canonical enumeration (registry, via crawl) — bounded pages.
    canonical_ids: set[str] = set()
    try:
        offset = 0
        for _ in range(10):
            rows = await crawl.artists(limit=200, offset=offset)
            if not rows:
                break
            for r in rows:
                cid = r.get("canonical_entity_id") or r.get("canonical_artist_id") or r.get("id")
                if cid:
                    canonical_ids.add(cid)
            if len(rows) < 200:
                break
            offset += 200
        canonical_available = True
    except Exception:  # noqa: BLE001 — degrade safely; a crawl outage must not break diagnostics
        canonical_available = False

    # graph ARTIST-node representation.
    try:
        graph_nodes = await GraphServiceClient().list_artist_nodes(limit=500)
        graph_node_count: int | None = len(graph_nodes)
    except Exception:  # noqa: BLE001
        graph_node_count = None

    # demand-ledger references.
    identity_ids = set(db.execute(
        select(func.distinct(ArtistExternalIdentity.canonical_artist_id))).scalars())
    from artist_intelligence_service.models import ArtistDemandObservation as _ADO
    observation_ids = set(db.execute(
        select(func.distinct(_ADO.canonical_artist_id))).scalars())

    referenced = identity_ids | observation_ids
    orphans = sorted(referenced - canonical_ids) if canonical_available else []
    with_evidence = int(db.execute(
        select(func.count(func.distinct(ArtistMarketEvidence.canonical_artist_id)))
        .where(ArtistMarketEvidence.evidence_class == "CONFIRMED_LIVE_INDIA")).scalar_one())

    return {
        "authoritative_source": "crawl entity-resolution registry (/v1/internal/entity-resolution/entities)",
        "canonical_registry_artists": len(canonical_ids) if canonical_available else None,
        "canonical_registry_available": canonical_available,
        "graph_artist_nodes": graph_node_count,
        "artists_referenced_by_demand_identities": len(identity_ids),
        "artists_referenced_by_demand_observations": len(observation_ids),
        "artists_with_confirmed_live_india": with_evidence,
        "orphan_demand_artist_references": len(orphans),
        "orphan_sample": orphans[:10],
        "note": "orphans are demand canonical_artist_id values not present in the authoritative "
                "canonical enumeration; reported for audit — never silently rewritten.",
    }


# late import to avoid a cycle at module import time
from artist_intelligence_service.models import DemandRefreshJob as _JOB  # noqa: E402
