"""Internal social evidence endpoints (Phase 5C.1).

Governed reads + associations + a bounded watchlist-driven acquisition run. Evidence only — nothing
here creates a canonical Event, and no raw post content is exposed or stored. Control routes are gated
by ``social_enabled``.
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from crawl_service.config import settings
from crawl_service.deps import get_social_service
from crawl_service.social import SocialAcquisitionService

router = APIRouter(prefix="/v1/internal/social", tags=["social (internal)"])


@router.post("/identities", summary="Associate a public social identity with a canonical entity")
def link_identity(payload: dict = Body(...),
                  svc: SocialAcquisitionService = Depends(get_social_service)) -> dict[str, Any]:
    try:
        return svc.link_identity(
            canonical_entity_id=str(payload.get("canonical_entity_id", "")),
            canonical_entity_type=str(payload.get("canonical_entity_type", "")),
            platform=str(payload.get("platform", "")), handle=str(payload.get("handle", "")),
            platform_account_id=payload.get("platform_account_id"),
            account_url=payload.get("account_url"), evidence_role=payload.get("evidence_role"),
            source=str(payload.get("source", "OPERATOR")), actor=payload.get("actor"))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/identities/{identity_id}/active", summary="Activate / deactivate a social identity")
def set_active(identity_id: str, payload: dict = Body(default={}),
               svc: SocialAcquisitionService = Depends(get_social_service)) -> dict[str, Any]:
    try:
        return svc.set_active(identity_id, active=bool(payload.get("active", True)),
                              actor=payload.get("actor"))
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/identities", summary="List social identities attached to canonical entities")
def identities(canonical_entity_id: str | None = Query(default=None),
               platform: str | None = Query(default=None),
               limit: int = Query(default=200, ge=1, le=500),
               svc: SocialAcquisitionService = Depends(get_social_service)) -> dict[str, Any]:
    return svc.identities(canonical_entity_id=canonical_entity_id, platform=platform, limit=limit)


@router.get("/mentions", summary="List social evidence (claims + provenance; no raw content)")
def mentions(canonical_entity_id: str | None = Query(default=None),
             platform: str | None = Query(default=None),
             processing_status: str | None = Query(default=None),
             current_only: bool = Query(default=True, description="current version per post only"),
             limit: int = Query(default=100, ge=1, le=500),
             svc: SocialAcquisitionService = Depends(get_social_service)) -> dict[str, Any]:
    return svc.mentions(canonical_entity_id=canonical_entity_id, platform=platform,
                        processing_status=processing_status, current_only=current_only, limit=limit)


@router.get("/mentions/history", summary="Immutable version lineage of one logical social post")
def mention_history(platform: str = Query(...), platform_post_id: str = Query(...),
                    svc: SocialAcquisitionService = Depends(get_social_service)) -> dict[str, Any]:
    return svc.mention_history(platform=platform, platform_post_id=platform_post_id)


@router.get("/coverage", summary="Social acquisition coverage + diagnostics")
def coverage(svc: SocialAcquisitionService = Depends(get_social_service)) -> dict[str, Any]:
    return svc.coverage()


@router.get("/watchlist", summary="Canonical entities eligible for social collection")
def watchlist(limit: int = Query(default=100, ge=1, le=500),
              svc: SocialAcquisitionService = Depends(get_social_service)) -> dict[str, Any]:
    return svc.watchlist(limit=limit)


@router.post("/run", summary="Run one bounded watchlist-driven social acquisition pass")
async def run(trace: bool = Query(default=False), limit: int | None = Query(default=None),
              svc: SocialAcquisitionService = Depends(get_social_service)) -> dict[str, Any]:
    if not settings.social_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="social acquisition disabled")
    return await svc.run_once(worker_id="api", limit=limit, trace=trace)
