"""Internal artist-intake / research-watchlist API (Phase 5B.1). Internal-only.

This is the one narrow operator-writable surface. The authenticated operator identity is supplied by the
gateway BFF as ``created_by`` (the demand service does not authenticate; the gateway does, against Google
Workspace). Every write records ``created_by``. These routes NEVER mutate canonical entities,
observations, graph nodes, provider observations, resolution outcomes, or event/historical state — they
only create/prioritise/pause/resume research targets, which then flow through the EXISTING resolution +
demand machinery.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from artist_intelligence_service import watchlist
from artist_intelligence_service.db import get_db

router = APIRouter(prefix="/v1/internal/watchlist", tags=["watchlist (research configuration)"])


class AddTargetRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=600)
    created_by: str = Field(min_length=1, max_length=320)
    canonical_artist_id: str | None = Field(default=None, max_length=512)
    youtube_hint: str | None = Field(default=None, max_length=1024)
    source: str = Field(default="OPERATOR", max_length=48)
    reason: str | None = Field(default=None, max_length=600)
    priority: int | None = Field(default=None, ge=0, le=40)


class BulkRequest(BaseModel):
    created_by: str = Field(min_length=1, max_length=320)
    text: str | None = Field(default=None, max_length=20000)
    names: list[str] | None = None
    reason: str | None = Field(default=None, max_length=600)


class PreviewRequest(BaseModel):
    text: str | None = Field(default=None, max_length=20000)
    names: list[str] | None = None


class PriorityRequest(BaseModel):
    priority: int = Field(ge=0, le=40)


class ReasonRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=600)


def _names(text: str | None, names: list[str] | None) -> list[str]:
    if names:
        joined = "\n".join(names)
    else:
        joined = text or ""
    return watchlist.parse_bulk_names(joined)


# ---- reads ------------------------------------------------------------------------------------
@router.get("", summary="List watch targets")
def list_targets(status_filter: str | None = Query(default=None, alias="status"),
                 limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    return watchlist.list_targets(db, status=status_filter, limit=limit, offset=offset)


@router.get("/diagnostics", summary="Watchlist coverage diagnostics")
def diagnostics(db: Session = Depends(get_db)) -> dict[str, Any]:
    return watchlist.diagnostics(db)


@router.get("/{target_id}", summary="Watch target detail")
def target_detail(target_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    target = watchlist.get_target(db, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="watch target not found")
    return watchlist.serialize(db, target)


# ---- writes (research configuration) ----------------------------------------------------------
@router.post("", summary="Add an artist to the watchlist (create + resolve)")
async def add_target(body: AddTargetRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        out = await watchlist.add_and_resolve(
            db, display_name=body.display_name, created_by=body.created_by,
            canonical_artist_id=body.canonical_artist_id, youtube_hint=body.youtube_hint,
            source=body.source, reason=body.reason, priority=body.priority)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    db.commit()
    return out


@router.post("/bulk/preview", summary="Preview a bulk intake (no writes)")
async def bulk_preview(body: PreviewRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return await watchlist.preview_bulk(db, _names(body.text, body.names))


@router.post("/bulk", summary="Bulk add artists to the watchlist (bounded, idempotent)")
async def bulk_add(body: BulkRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    out = await watchlist.add_bulk(db, _names(body.text, body.names), created_by=body.created_by,
                                   reason=body.reason)
    db.commit()
    return out


@router.post("/reresolve", summary="Bounded re-resolution pass over pending targets")
async def reresolve(limit: int = Query(default=25, ge=1, le=200),
                    db: Session = Depends(get_db)) -> dict[str, Any]:
    out = await watchlist.reresolve_pending(db, limit=limit)
    db.commit()
    return out


def _require(db: Session, target_id: str):
    target = watchlist.get_target(db, target_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="watch target not found")
    return target


@router.post("/{target_id}/pause", summary="Pause a watch target (suspend recurring collection)")
def pause(target_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    out = watchlist.pause_target(db, _require(db, target_id))
    db.commit()
    return out


@router.post("/{target_id}/resume", summary="Resume a paused watch target")
async def resume(target_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    out = await watchlist.resume_target(db, _require(db, target_id))
    db.commit()
    return out


@router.post("/{target_id}/priority", summary="Set watch priority")
def priority(target_id: str, body: PriorityRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    out = watchlist.set_priority(db, _require(db, target_id), body.priority)
    db.commit()
    return out


@router.post("/{target_id}/reject", summary="Remove (reject) a watch target")
def reject(target_id: str, body: ReasonRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    out = watchlist.reject_target(db, _require(db, target_id), reason=body.reason)
    db.commit()
    return out
