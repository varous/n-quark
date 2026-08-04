"""Admin BFF routes (`/admin/v1/...`). Read-only except two narrow, audited OPERATOR actions.

Every route enforces authentication + role authorization server-side. The whole surface is gated by
`ADMIN_API_ENABLED` (via the auth dependency) and returns 404 when disabled.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from api_gateway.admin import auth
from api_gateway.admin.deps import get_admin_service, get_audit_store
from api_gateway.admin.service import AdminService
from api_gateway.config import settings

router = APIRouter(prefix="/admin/v1", tags=["admin"])


# ---- auth (dev mode) ----------------------------------------------------------------------------
@router.post("/auth/login", summary="Development login (isolated dev auth only)")
def login(payload: dict = Body(...)) -> dict[str, Any]:
    if not settings.admin_api_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="admin api disabled")
    if not settings.admin_dev_auth_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="dev auth disabled; a production identity provider is required")
    username = str(payload.get("username", "")).strip()
    role = str(payload.get("role", "VIEWER")).strip().upper()
    if not username:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="username required")
    if role not in auth.ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"role must be one of {auth.ROLES}")
    token = auth.issue_dev_token(username, role)
    return {"token": token, "sub": username, "role": role,
            "expires_in": settings.admin_session_ttl_seconds, "auth_mode": "dev"}


@router.get("/auth/me", summary="Current principal")
def me(principal: auth.Principal = Depends(auth.require_viewer)) -> dict[str, Any]:
    return {"sub": principal.sub, "role": principal.role, "auth_mode": principal.auth_mode}


# ---- read models (VIEWER) -----------------------------------------------------------------------
@router.get("/dashboard")
async def dashboard(_: auth.Principal = Depends(auth.require_viewer),
                    svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.dashboard()


@router.get("/sources")
async def sources(_: auth.Principal = Depends(auth.require_viewer),
                  svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.sources()


@router.get("/sources/{source}")
async def source_detail(source: str, _: auth.Principal = Depends(auth.require_viewer),
                        svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.source_detail(source)


@router.get("/events")
async def events(source: str | None = Query(default=None),
                 stale_only: bool = Query(default=False),
                 has_transitions: bool = Query(default=False),
                 limit: int = Query(default=25, ge=1, le=100),
                 offset: int = Query(default=0, ge=0),
                 _: auth.Principal = Depends(auth.require_viewer),
                 svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.events(source=source, stale_only=stale_only,
                            has_transitions=has_transitions, limit=limit, offset=offset)


@router.get("/events/{event_id}")
async def event_detail(event_id: str, _: auth.Principal = Depends(auth.require_viewer),
                       svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.event_detail(event_id)


@router.get("/events/{event_id}/timeline")
async def event_timeline(event_id: str, raw: bool = Query(default=False),
                         _: auth.Principal = Depends(auth.require_viewer),
                         svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.event_timeline(event_id, raw=raw)


@router.get("/events/{event_id}/evidence")
async def event_evidence(event_id: str, _: auth.Principal = Depends(auth.require_viewer),
                         svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.event_evidence(event_id)


@router.get("/events/{event_id}/relationships")
async def event_relationships(event_id: str, _: auth.Principal = Depends(auth.require_viewer),
                              svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    detail = await svc.event_detail(event_id)
    return {"canonical_event_id": event_id, "relationships": detail.get("relationships", [])}


@router.get("/entities")
async def entities(entity_type: str | None = Query(default=None),
                   source: str | None = Query(default=None),
                   status_filter: str | None = Query(default=None, alias="status"),
                   cross_source_only: bool = Query(default=False),
                   has_ambiguous: bool = Query(default=False),
                   limit: int = Query(default=50, ge=1, le=200),
                   offset: int = Query(default=0, ge=0),
                   _: auth.Principal = Depends(auth.require_viewer),
                   svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.entities(entity_type=entity_type, source=source, status=status_filter,
                              cross_source_only=cross_source_only, has_ambiguous=has_ambiguous,
                              limit=limit, offset=offset)


@router.get("/entities/{entity_type}/{entity_id}")
async def entity_detail(entity_type: str, entity_id: str,
                        _: auth.Principal = Depends(auth.require_viewer),
                        svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.entity_detail(entity_type, entity_id)


@router.get("/resolution-queue")
async def resolution_queue(entity_type: str | None = Query(default=None),
                           source: str | None = Query(default=None),
                           limit: int = Query(default=50, ge=1, le=200),
                           _: auth.Principal = Depends(auth.require_viewer),
                           svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.resolution_queue(entity_type=entity_type, source=source, limit=limit)


@router.get("/resolution-queue/candidates/{candidate_id}")
async def candidate_detail(candidate_id: str, _: auth.Principal = Depends(auth.require_viewer),
                           svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.candidate_detail(candidate_id)


@router.get("/capture-jobs")
async def capture_jobs(status_filter: str | None = Query(default=None, alias="status"),
                       source: str | None = Query(default=None),
                       expired_lock: bool = Query(default=False),
                       limit: int = Query(default=50, ge=1, le=200),
                       offset: int = Query(default=0, ge=0),
                       _: auth.Principal = Depends(auth.require_viewer),
                       svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.capture_jobs(status=status_filter, source=source, expired_lock=expired_lock,
                                  limit=limit, offset=offset)


@router.get("/capture-jobs/{job_id}")
async def capture_job(job_id: str, _: auth.Principal = Depends(auth.require_viewer),
                      svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.capture_job(job_id)


@router.get("/graph/subgraph")
async def subgraph(root: str = Query(...), depth: int = Query(default=1, ge=0),
                   rel_types: str | None = Query(default=None),
                   _: auth.Principal = Depends(auth.require_viewer),
                   svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    rels = {r.strip() for r in rel_types.split(",")} if rel_types else None
    return await svc.subgraph(root, depth=depth, rel_types=rels)


@router.get("/system-health")
async def system_health(_: auth.Principal = Depends(auth.require_viewer),
                        svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.system_health()


@router.get("/search")
async def search(q: str = Query(...), limit: int = Query(default=20, ge=1, le=100),
                 _: auth.Principal = Depends(auth.require_viewer),
                 svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.search(q, limit=limit)


# ---- audit (ADMIN) ------------------------------------------------------------------------------
@router.get("/audit")
def audit_log(actor: str | None = Query(default=None), action: str | None = Query(default=None),
              object_type: str | None = Query(default=None), object_id: str | None = Query(default=None),
              request_id: str | None = Query(default=None),
              limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
              _: auth.Principal = Depends(auth.require_admin),
              store=Depends(get_audit_store)) -> dict[str, Any]:
    return store.list(actor=actor, action=action, object_type=object_type, object_id=object_id,
                      request_id=request_id, limit=limit, offset=offset)


# ---- narrow operational actions (OPERATOR; flag-gated; audited) ---------------------------------
def _require_operations() -> None:
    if not settings.admin_operational_actions_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="operational actions disabled")


@router.post("/operations/rerun-enrichment")
async def rerun_enrichment(payload: dict = Body(...),
                           principal: auth.Principal = Depends(auth.require_operator),
                           svc: AdminService = Depends(get_admin_service),
                           store=Depends(get_audit_store)) -> dict[str, Any]:
    _require_operations()
    event_id = str(payload.get("event_id", "")).strip()
    if not event_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="event_id required")
    request_id = uuid.uuid4().hex
    result = await svc.rerun_enrichment(event_id)
    store.record(actor_id=principal.sub, actor_role=principal.role, action="RERUN_ENRICHMENT",
                 object_type="event", object_id=event_id, request_id=request_id,
                 new_value={"ok": result["ok"], "status": result["status"]},
                 reason=payload.get("reason"))
    return {"request_id": request_id, **result}


@router.post("/operations/capture-now")
async def capture_now(payload: dict = Body(...),
                      principal: auth.Principal = Depends(auth.require_operator),
                      svc: AdminService = Depends(get_admin_service),
                      store=Depends(get_audit_store)) -> dict[str, Any]:
    _require_operations()
    source = str(payload.get("source", "")).strip()
    sid = str(payload.get("source_record_id", "")).strip()
    if not (source and sid):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="source and source_record_id required")
    request_id = uuid.uuid4().hex
    result = await svc.capture_now(source, sid, payload.get("canonical_event_id"))
    store.record(actor_id=principal.sub, actor_role=principal.role, action="CAPTURE_NOW",
                 object_type="tracked_event", object_id=f"{source}:{sid}", request_id=request_id,
                 new_value={"ok": result["ok"], "status": result["status"]},
                 reason=payload.get("reason"))
    return {"request_id": request_id, **result}


@router.post("/operations/rerun-entity-resolution")
async def rerun_entity_resolution(payload: dict = Body(...),
                                  principal: auth.Principal = Depends(auth.require_operator),
                                  svc: AdminService = Depends(get_admin_service),
                                  store=Depends(get_audit_store)) -> dict[str, Any]:
    _require_operations()
    event_id = str(payload.get("event_id", "")).strip()
    source = str(payload.get("source", "")).strip()
    sid = str(payload.get("source_record_id", "")).strip()
    if not (event_id and source and sid):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="event_id, source, source_record_id required")
    request_id = uuid.uuid4().hex
    result = await svc.rerun_entity_resolution(event_id, source, sid)
    store.record(actor_id=principal.sub, actor_role=principal.role, action="RERUN_ENTITY_RESOLUTION",
                 object_type="event", object_id=event_id, request_id=request_id,
                 new_value={"ok": result["ok"], "status": result["status"]},
                 reason=payload.get("reason"))
    return {"request_id": request_id, **result}
