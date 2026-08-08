"""Internal demand-intelligence API (Phase 5A). Internal-only; no public/customer surface."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from artist_intelligence_service import intelligence, reads
from artist_intelligence_service.db import get_db
from artist_intelligence_service.deps import get_service
from artist_intelligence_service.providers.google_trends import TrendsImportError
from artist_intelligence_service.service import DemandService, QuotaExhausted

router = APIRouter(prefix="/v1/internal", tags=["demand-intelligence (internal)"])


# ---- request bodies ---------------------------------------------------------------------------
class YouTubeResolveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=256)
    hints: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=5, ge=1, le=25)


class TrendsImportRequest(BaseModel):
    canonical_artist_id: str = Field(min_length=1, max_length=512)
    identity_type: str = Field(description="SEARCH_TERM or TOPIC_ID")
    provider_id: str = Field(min_length=1, max_length=600)
    geo: str = Field(default="IN", max_length=16)
    csv_text: str = Field(min_length=1)
    time_range: str | None = None
    comparison_window: str | None = None
    source_file: str | None = None
    export_timestamp: str | None = None


# ---- identity ---------------------------------------------------------------------------------
@router.get("/artists/{artist_id}/external-identities", summary="External platform identities")
def external_identities(artist_id: str, db: Session = Depends(get_db),
                        svc: DemandService = Depends(get_service)) -> dict[str, Any]:
    return {"canonical_artist_id": artist_id,
            "identities": [intelligence._identity_dict(i) for i in svc.list_identities(db, artist_id)]}


@router.post("/artists/{artist_id}/youtube/resolve", summary="Resolve a YouTube identity (bounded search)")
async def youtube_resolve(artist_id: str, body: YouTubeResolveRequest,
                          db: Session = Depends(get_db),
                          svc: DemandService = Depends(get_service)) -> dict[str, Any]:
    try:
        out = await svc.resolve_youtube(db, artist_id, query=body.query, hints=body.hints,
                                        limit=body.limit)
        db.commit()
        return out
    except QuotaExhausted as exc:
        db.commit()  # quota accounting already recorded
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc


@router.get("/artists/{artist_id}/youtube", summary="YouTube demand snapshot + deltas")
async def youtube_demand(artist_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    demand = await intelligence.build_demand(db, artist_id)
    return {"canonical_artist_id": artist_id, "external_identities": demand["external_identities"],
            "youtube": demand["youtube"]}


@router.post("/artists/{artist_id}/youtube/refresh", summary="Refresh YouTube snapshot by known id")
async def youtube_refresh(artist_id: str, include_videos: bool = Query(default=True),
                          db: Session = Depends(get_db),
                          svc: DemandService = Depends(get_service)) -> dict[str, Any]:
    out = await svc.snapshot_youtube(db, artist_id, include_videos=include_videos)
    db.commit()
    return out


# ---- trends -----------------------------------------------------------------------------------
@router.post("/trends/import", summary="Import a Google Trends CSV export")
def trends_import(body: TrendsImportRequest, db: Session = Depends(get_db),
                  svc: DemandService = Depends(get_service)) -> dict[str, Any]:
    try:
        out = svc.import_trends(
            db, body.canonical_artist_id, csv_text=body.csv_text, identity_type=body.identity_type,
            provider_id=body.provider_id, geo=body.geo, time_range=body.time_range,
            comparison_window=body.comparison_window, source_file=body.source_file,
            export_timestamp=body.export_timestamp)
        db.commit()
        return out
    except (TrendsImportError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/artists/{artist_id}/trends", summary="Google Trends demand (interest + geography)")
async def trends_demand(artist_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    demand = await intelligence.build_demand(db, artist_id)
    return {"canonical_artist_id": artist_id, "google_trends": demand["google_trends"]}


# ---- read models ------------------------------------------------------------------------------
@router.get("/artists/{artist_id}/demand", summary="Supply + demand juxtaposition")
async def demand(artist_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return await intelligence.build_demand(db, artist_id)


@router.get("/artists/{artist_id}/momentum", summary="Deterministic momentum components")
def momentum(artist_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return intelligence.build_momentum(db, artist_id)


@router.get("/artists/{artist_id}/geography", summary="Artist × region demand + supply")
async def geography(artist_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return await intelligence.build_geography(db, artist_id)


@router.get("/artists/{artist_id}/event-response", summary="Event-relative co-movement timeline")
async def event_response(artist_id: str, event_id: str = Query(..., min_length=1),
                         db: Session = Depends(get_db)) -> dict[str, Any]:
    return await intelligence.build_event_response(db, artist_id, event_id)


@router.get("/artists/{artist_id}/observations", summary="Bounded, filterable observation history")
def observations(artist_id: str, provider: str | None = Query(default=None),
                 metric: str | None = Query(default=None), scope_id: str | None = Query(default=None),
                 date_from: datetime | None = Query(default=None),
                 date_to: datetime | None = Query(default=None),
                 limit: int = Query(default=100, ge=1, le=1000),
                 offset: int = Query(default=0, ge=0),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"canonical_artist_id": artist_id,
            **reads.list_observations(db, artist_id, provider=provider, metric=metric,
                                      scope_id=scope_id, date_from=date_from, date_to=date_to,
                                      limit=limit, offset=offset)}


# ---- diagnostics ------------------------------------------------------------------------------
@router.get("/demand/coverage", summary="Coverage + epistemic diagnostics")
async def coverage(db: Session = Depends(get_db)) -> dict[str, Any]:
    return await intelligence.build_coverage(db)


@router.get("/demand/provider-health", summary="Provider health + Trends mode + YouTube REAL/MOCK")
async def provider_health(db: Session = Depends(get_db)) -> dict[str, Any]:
    return await intelligence.build_provider_health_full(db)


@router.get("/demand/quota", summary="Per-provider/day quota accounting")
def quota(db: Session = Depends(get_db)) -> dict[str, Any]:
    return intelligence.build_quota(db)


@router.get("/demand/scheduler", summary="Read-only demand refresh scheduler state")
def scheduler_state(db: Session = Depends(get_db)) -> dict[str, Any]:
    return intelligence.build_scheduler_state(db)


@router.get("/demand/quota-buckets", summary="Per-bucket YouTube quota accounting (5A.3)")
def quota_buckets(db: Session = Depends(get_db)) -> dict[str, Any]:
    return intelligence.build_quota_buckets(db)


@router.get("/demand/artist-universe", summary="Artist-universe coverage diagnostics (5A.3)")
async def artist_universe(db: Session = Depends(get_db)) -> dict[str, Any]:
    from artist_intelligence_service import universe
    return await universe.build_artist_universe(db)


# ---- artist universe: onboarding + backfill + discovery (Phase 5A.3) ---------------------------
class OnboardRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=600)
    source_id: str | None = None
    evidence_class: str | None = "CONFIRMED_LIVE_INDIA"
    evidence_source: str = "EVENT"
    evidence_ref: str | None = None


@router.post("/artists/{artist_id}/onboard", summary="Onboard a canonical artist into demand resolution")
def onboard(artist_id: str, body: OnboardRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    from artist_intelligence_service import universe
    out = universe.onboard_artist(
        db, canonical_artist_id=artist_id, display_name=body.display_name, source_id=body.source_id,
        evidence_class=body.evidence_class, evidence_source=body.evidence_source,
        evidence_ref=body.evidence_ref)
    db.commit()
    return out


@router.post("/backfill/artists", summary="Queue existing canonical artists lacking a YouTube identity")
async def backfill_artists(limit: int = Query(default=50, ge=1, le=500),
                           db: Session = Depends(get_db)) -> dict[str, Any]:
    from artist_intelligence_service import universe
    out = await universe.backfill_missing_identities(db, limit=limit)
    db.commit()
    return out


@router.post("/discovery/youtube/run", summary="Run bounded YouTube artist discovery (candidates only)")
async def run_discovery(max_queries: int | None = Query(default=None, ge=1, le=25),
                        db: Session = Depends(get_db),
                        svc: DemandService = Depends(get_service)) -> dict[str, Any]:
    from artist_intelligence_service import discovery
    out = await discovery.run_youtube_discovery(db, provider=svc.youtube, max_queries=max_queries)
    db.commit()
    return out
