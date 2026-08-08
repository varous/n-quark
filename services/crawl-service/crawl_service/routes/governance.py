"""Internal governed entity-resolution commands (Admin Phase B). Called by the gateway admin BFF only.

Flag-gated (entity_resolution_enabled). Validation errors -> 400, concurrency/linkage conflicts -> 409.
These perform the actual entity/graph mutations via the reused Phase 3.1 pathways; the gateway owns the
role authorization, audit and append-only decision records.
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status

from crawl_service.config import settings
from crawl_service.deps import get_governance_service
from crawl_service.governance import GovernanceConflict, GovernanceError, GovernanceService

router = APIRouter(prefix="/v1/internal/governance", tags=["governance (internal)"])


def _guard() -> None:
    if not settings.entity_resolution_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="entity resolution disabled")


def _handle(exc: GovernanceError):
    code = status.HTTP_409_CONFLICT if isinstance(exc, GovernanceConflict) else status.HTTP_400_BAD_REQUEST
    raise HTTPException(code, detail={"code": exc.code, "detail": exc.detail}) from exc


@router.post("/preview")
async def preview(payload: dict = Body(...), svc: GovernanceService = Depends(get_governance_service)) -> dict[str, Any]:
    _guard()
    try:
        return await svc.preview(**payload)
    except GovernanceError as exc:
        _handle(exc)


@router.post("/accept")
async def accept(payload: dict = Body(...), svc: GovernanceService = Depends(get_governance_service)) -> dict[str, Any]:
    _guard()
    try:
        return await svc.accept(**payload)
    except GovernanceError as exc:
        _handle(exc)


@router.post("/reject")
def reject(payload: dict = Body(...), svc: GovernanceService = Depends(get_governance_service)) -> dict[str, Any]:
    _guard()
    try:
        return svc.reject(**payload)
    except GovernanceError as exc:
        _handle(exc)


@router.post("/mark-unresolved")
def mark_unresolved(payload: dict = Body(...), svc: GovernanceService = Depends(get_governance_service)) -> dict[str, Any]:
    _guard()
    try:
        return svc.mark_unresolved(**payload)
    except GovernanceError as exc:
        _handle(exc)


@router.post("/create-entity")
async def create_entity(payload: dict = Body(...), svc: GovernanceService = Depends(get_governance_service)) -> dict[str, Any]:
    _guard()
    try:
        return await svc.create_entity(**payload)
    except GovernanceError as exc:
        _handle(exc)


@router.post("/create-artist")
async def create_artist(payload: dict = Body(...),
                        svc: GovernanceService = Depends(get_governance_service)) -> dict[str, Any]:
    """Phase 5A.3.1 — create/match a canonical ARTIST from external discovery evidence (crawl-owned)."""
    _guard()
    try:
        return await svc.create_artist(**payload)
    except GovernanceError as exc:
        _handle(exc)


@router.post("/link-handle")
async def link_handle(payload: dict = Body(...), svc: GovernanceService = Depends(get_governance_service)) -> dict[str, Any]:
    _guard()
    try:
        return await svc.accept(reason_code="MANUAL_LINK", **payload)
    except GovernanceError as exc:
        _handle(exc)


@router.post("/supersede-legacy")
async def supersede_legacy(payload: dict = Body(...), svc: GovernanceService = Depends(get_governance_service)) -> dict[str, Any]:
    _guard()
    try:
        return await svc.supersede_legacy(**payload)
    except GovernanceError as exc:
        _handle(exc)


@router.post("/unsupersede")
async def unsupersede(payload: dict = Body(...), svc: GovernanceService = Depends(get_governance_service)) -> dict[str, Any]:
    _guard()
    return await svc.unsupersede(**payload)


@router.post("/correct-series")
async def correct_series(payload: dict = Body(...), svc: GovernanceService = Depends(get_governance_service)) -> dict[str, Any]:
    _guard()
    try:
        return await svc.correct_series(**payload)
    except GovernanceError as exc:
        _handle(exc)


@router.post("/reverse-accept")
async def reverse_accept(payload: dict = Body(...), svc: GovernanceService = Depends(get_governance_service)) -> dict[str, Any]:
    _guard()
    try:
        return await svc.reverse_accept(**payload)
    except GovernanceError as exc:
        _handle(exc)


@router.get("/counts")
async def counts(svc: GovernanceService = Depends(get_governance_service)) -> dict[str, Any]:
    return await svc.governance_counts()
