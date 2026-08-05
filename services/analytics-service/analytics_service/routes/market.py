"""Canonical market read-model endpoints (Phase 4A).

Deterministic, bounded, paginated aggregates over observed Boshow/District data. Entities are counted
by canonical id (legacy projections folded via the canonical projection). No prediction, no scores, no
total-market claim. `trace=true` explains inclusion/exclusion, canonicalization and metric definitions.

These live under `/v1/analytics/market/...` and do not touch the existing scoring endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from analytics_service import readmodels as rm
from analytics_service.datasource import AnalyticsDataSource
from analytics_service.deps import get_datasource

router = APIRouter(prefix="/v1/analytics/market", tags=["market read models"])


# ---- shared query facets ------------------------------------------------------------------------
class Facets:
    def __init__(
        self,
        date_from: str | None = Query(default=None),
        date_to: str | None = Query(default=None),
        source: str | None = Query(default=None),
        city: str | None = Query(default=None),
        region: str | None = Query(default=None),
        trace: bool = Query(default=False),
    ) -> None:
        self.date_from, self.date_to = date_from, date_to
        self.source, self.city, self.region = source, city, region
        self.trace = trace

    def scope(self, events: list[rm.ObservedEvent]) -> rm.ScopedEvents:
        return rm.scope_events(events, date_from=self.date_from, date_to=self.date_to,
                               source=self.source, city=self.city, region=self.region)


def _paginate(rows: list, limit: int, offset: int) -> dict[str, Any]:
    return {"count": len(rows), "limit": limit, "offset": offset, "items": rows[offset:offset + limit]}


def _envelope(ds: rm.Dataset, scoped: rm.ScopedEvents, facets: Facets, payload: dict[str, Any],
              metric_definitions: dict[str, str], scope_extra: dict | None = None) -> dict[str, Any]:
    out = {**payload, "scope": rm.scope_block(ds, scope_extra)}
    if facets.trace:
        out["trace"] = rm.build_trace(ds, scoped, metric_definitions=metric_definitions)
    return out


DEFS = {
    "observed_event_count": "Distinct events captured from configured sources in scope.",
    "unique_canonical_*": "Distinct entities after folding legacy/superseded ids to their canonical id.",
    "upcoming/completed": "By event start time relative to `as_of`; undated events excluded from both.",
    "observed supply": "What n-quark captured — not total market activity.",
}


# ---- canonical projection (section 1) -----------------------------------------------------------
@router.get("/canonicalize/{entity_id:path}", summary="Resolve an entity id to its canonical id")
async def canonicalize(entity_id: str, ds: AnalyticsDataSource = Depends(get_datasource)) -> dict[str, Any]:
    dataset = await ds.load()
    c = dataset.canonicalizer.resolve(entity_id)
    return {
        "input_entity_id": c.input_entity_id,
        "canonical_entity_id": c.canonical_entity_id,
        "resolution_path": c.resolution_path,
        "identity_state": c.identity_state,
        "warnings": c.warnings,
        "scope": rm.scope_block(dataset),
    }


# ---- regions --------------------------------------------------------------------------------------
@router.get("/regions", summary="Observed event supply by region/city")
async def regions(facets: Facets = Depends(), limit: int = Query(50, ge=1, le=200),
                  offset: int = Query(0, ge=0),
                  ds: AnalyticsDataSource = Depends(get_datasource)) -> dict[str, Any]:
    dataset = await ds.load()
    scoped = facets.scope(dataset.events)
    rows = rm.regional_supply(dataset, scoped)
    return _envelope(dataset, scoped, facets, _paginate(rows, limit, offset), DEFS)


@router.get("/regions/{region_id:path}", summary="Observed supply for one region/city")
async def region_one(region_id: str, facets: Facets = Depends(),
                     ds: AnalyticsDataSource = Depends(get_datasource)) -> dict[str, Any]:
    dataset = await ds.load()
    scoped = facets.scope(dataset.events)
    row = rm.region_detail(dataset, region_id, scoped)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no observed events for region in scope")
    return _envelope(dataset, scoped, facets, row, DEFS)


# ---- artists --------------------------------------------------------------------------------------
@router.get("/artists", summary="Observed artist activity")
async def artists(facets: Facets = Depends(), limit: int = Query(50, ge=1, le=200),
                  offset: int = Query(0, ge=0),
                  ds: AnalyticsDataSource = Depends(get_datasource)) -> dict[str, Any]:
    dataset = await ds.load()
    scoped = facets.scope(dataset.events)
    rows = rm.list_activity(dataset, "ARTIST", rm.artist_activity, scoped)
    return _envelope(dataset, scoped, facets, _paginate(rows, limit, offset), DEFS)


@router.get("/artists/{artist_id:path}", summary="Observed activity for one canonical artist")
async def artist_one(artist_id: str, facets: Facets = Depends(),
                     ds: AnalyticsDataSource = Depends(get_datasource)) -> dict[str, Any]:
    dataset = await ds.load()
    scoped = facets.scope(dataset.events)
    row = rm.artist_activity(dataset, dataset.canonicalizer.canonical_id(artist_id), scoped)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="artist not found")
    return _envelope(dataset, scoped, facets, row, DEFS)


# ---- venues ---------------------------------------------------------------------------------------
@router.get("/venues", summary="Observed venue activity")
async def venues(facets: Facets = Depends(), limit: int = Query(50, ge=1, le=200),
                 offset: int = Query(0, ge=0),
                 ds: AnalyticsDataSource = Depends(get_datasource)) -> dict[str, Any]:
    dataset = await ds.load()
    scoped = facets.scope(dataset.events)
    rows = rm.list_activity(dataset, "VENUE", rm.venue_activity, scoped)
    return _envelope(dataset, scoped, facets, _paginate(rows, limit, offset), DEFS)


@router.get("/venues/{venue_id:path}", summary="Observed activity for one canonical venue")
async def venue_one(venue_id: str, facets: Facets = Depends(),
                    ds: AnalyticsDataSource = Depends(get_datasource)) -> dict[str, Any]:
    dataset = await ds.load()
    scoped = facets.scope(dataset.events)
    row = rm.venue_activity(dataset, dataset.canonicalizer.canonical_id(venue_id), scoped)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="venue not found")
    return _envelope(dataset, scoped, facets, row, DEFS)


# ---- organizers -----------------------------------------------------------------------------------
@router.get("/organizers", summary="Observed organizer activity")
async def organizers(facets: Facets = Depends(), limit: int = Query(50, ge=1, le=200),
                     offset: int = Query(0, ge=0),
                     ds: AnalyticsDataSource = Depends(get_datasource)) -> dict[str, Any]:
    dataset = await ds.load()
    scoped = facets.scope(dataset.events)
    rows = rm.list_activity(dataset, "ORGANIZER", rm.organizer_activity, scoped)
    return _envelope(dataset, scoped, facets, _paginate(rows, limit, offset), DEFS)


@router.get("/organizers/{organizer_id:path}", summary="Observed activity for one canonical organizer")
async def organizer_one(organizer_id: str, facets: Facets = Depends(),
                        ds: AnalyticsDataSource = Depends(get_datasource)) -> dict[str, Any]:
    dataset = await ds.load()
    scoped = facets.scope(dataset.events)
    row = rm.organizer_activity(dataset, dataset.canonicalizer.canonical_id(organizer_id), scoped)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="organizer not found")
    return _envelope(dataset, scoped, facets, row, DEFS)


# ---- series ---------------------------------------------------------------------------------------
@router.get("/series", summary="Observed event-series activity (strong markers only)")
async def series(facets: Facets = Depends(), limit: int = Query(50, ge=1, le=200),
                 offset: int = Query(0, ge=0),
                 ds: AnalyticsDataSource = Depends(get_datasource)) -> dict[str, Any]:
    dataset = await ds.load()
    scoped = facets.scope(dataset.events)
    rows = rm.list_series(dataset, scoped)
    return _envelope(dataset, scoped, facets, _paginate(rows, limit, offset), DEFS)


@router.get("/series/{series_id:path}", summary="Observed activity for one canonical event series")
async def series_one(series_id: str, facets: Facets = Depends(),
                     ds: AnalyticsDataSource = Depends(get_datasource)) -> dict[str, Any]:
    dataset = await ds.load()
    scoped = facets.scope(dataset.events)
    row = rm.series_activity(dataset, dataset.canonicalizer.canonical_id(series_id), scoped)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="series not found or excluded (weak/superseded)")
    return _envelope(dataset, scoped, facets, row, DEFS)


# ---- observation quality --------------------------------------------------------------------------
@router.get("/observation-quality", summary="Strength of the underlying observed dataset")
async def observation_quality(facets: Facets = Depends(),
                              by: str | None = Query(default=None, pattern="^(source|region)$"),
                              ds: AnalyticsDataSource = Depends(get_datasource)) -> dict[str, Any]:
    dataset = await ds.load()
    scoped = facets.scope(dataset.events)
    payload = rm.observation_quality(dataset, scoped, by=by)
    return _envelope(dataset, scoped, facets, payload, DEFS,
                     scope_extra={"note": "Measures observation quality, not market coverage."})


# ---- commercial state -----------------------------------------------------------------------------
@router.get("/commercial-state", summary="Observed commercial-state summaries (Shadow Ledger facts only)")
async def commercial_state(facets: Facets = Depends(),
                           ds: AnalyticsDataSource = Depends(get_datasource)) -> dict[str, Any]:
    dataset = await ds.load()
    scoped = facets.scope(dataset.events)
    payload = rm.commercial_state(dataset, scoped)
    return _envelope(dataset, scoped, facets, payload, DEFS,
                     scope_extra={"note": "Only observed price/availability/status facts; no sales or "
                                          "sell-through estimated."})
