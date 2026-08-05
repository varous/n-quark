"""Internal creative-observation API (Phase 4B). No public creative-intelligence surface.

Everything is gated by MEDIA_OBSERVATION_ENABLED (503 when off), so existing behaviour is unchanged
when the feature is disabled.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from media_service.config import settings
from media_service.deps import get_media_reads, get_media_service
from media_service.reads import MediaReads
from media_service.service import MediaService, ObserveInput

router = APIRouter(prefix="/v1/internal/media", tags=["media (internal)"])


def _require_enabled() -> None:
    if not settings.media_observation_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="media observation disabled")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="bad datetime") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@router.post("/observe", summary="Observe a creative asset reference for an event")
async def observe(payload: dict = Body(...),
                  svc: MediaService = Depends(get_media_service)) -> dict[str, Any]:
    _require_enabled()
    event_id = str(payload.get("canonical_event_id", "")).strip()
    source = str(payload.get("source", "")).strip()
    if not (event_id and source):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="canonical_event_id and source required")
    if source not in settings.media_source_set:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"source not enabled for media ({sorted(settings.media_source_set)})")
    asset_url = payload.get("asset_url")
    role = payload.get("asset_role", "UNKNOWN")
    # optional: resolve the asset reference from the canonical event node the capture wrote
    if not asset_url and payload.get("resolve_from_graph") and svc.graph is not None:
        node = await svc.graph.get_node(event_id)
        img = ((node or {}).get("properties") or {}).get("image_url")
        if img:
            asset_url = img
            if not payload.get("asset_role"):
                role = "POSTER"  # the event's primary listing image
    inp = ObserveInput(
        canonical_event_id=event_id, source=source, asset_url=asset_url or None,
        asset_role=role or "UNKNOWN", source_record_id=payload.get("source_record_id"),
        observed_at=_parse_dt(payload.get("observed_at")),
        source_page_url=payload.get("source_page_url"),
        trace_id=payload.get("trace_id") or payload.get("capture_id"),
        authoritative=bool(payload.get("authoritative", False)))
    return await svc.observe(inp)


@router.get("/assets", summary="List content-addressed assets")
def list_assets(source: str | None = Query(default=None),
                fetch_status: str | None = Query(default=None),
                limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                reads: MediaReads = Depends(get_media_reads)) -> dict[str, Any]:
    _require_enabled()
    return reads.list_assets(source=source, fetch_status=fetch_status, limit=limit, offset=offset)


@router.get("/assets/{asset_id}", summary="One asset + its observations/events")
def get_asset(asset_id: str, reads: MediaReads = Depends(get_media_reads)) -> dict[str, Any]:
    _require_enabled()
    data = reads.get_asset(asset_id)
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="asset not found")
    return data


@router.get("/events/{event_id}", summary="Current creatives for one event")
def event_assets(event_id: str, reads: MediaReads = Depends(get_media_reads)) -> dict[str, Any]:
    _require_enabled()
    return reads.event_assets(event_id)


@router.get("/events/{event_id}/timeline", summary="Creative-change timeline for one event")
def event_timeline(event_id: str, source: str | None = Query(default=None),
                   asset_role: str | None = Query(default=None),
                   changed_only: bool = Query(default=False),
                   limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
                   reads: MediaReads = Depends(get_media_reads)) -> dict[str, Any]:
    _require_enabled()
    return reads.event_timeline(event_id, source=source, asset_role=asset_role,
                                changed_only=changed_only, limit=limit, offset=offset)


@router.get("/events/{event_id}/creative-summary", summary="Stable analytics read contract")
def creative_summary(event_id: str, reads: MediaReads = Depends(get_media_reads)) -> dict[str, Any]:
    _require_enabled()
    return reads.event_creative_summary(event_id)


@router.get("/coverage", summary="Observed creative coverage by source")
def coverage(source: str | None = Query(default=None),
             reads: MediaReads = Depends(get_media_reads)) -> dict[str, Any]:
    _require_enabled()
    return reads.coverage(source=source)


@router.get("/failures", summary="Failed fetch observations by class")
def failures(source: str | None = Query(default=None), error_class: str | None = Query(default=None),
             limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
             reads: MediaReads = Depends(get_media_reads)) -> dict[str, Any]:
    _require_enabled()
    return reads.failures(source=source, error_class=error_class, limit=limit, offset=offset)
