"""Admin BFF routes (`/admin/v1/...`). Read-only except two narrow, audited OPERATOR actions.

Every route enforces authentication + role authorization server-side. The whole surface is gated by
`ADMIN_API_ENABLED` (via the auth dependency) and returns 404 when disabled.
"""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from typing import Any

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, Response

from api_gateway.admin import auth, oidc
from api_gateway.admin.catalog import CatalogAdminService
from api_gateway.admin.demand import DemandAdminService
from api_gateway.admin.deps import (
    get_admin_service,
    get_audit_store,
    get_catalog_service,
    get_demand_service,
    get_social_service,
    get_watchlist_service,
)
from api_gateway.admin.service import AdminService
from api_gateway.admin.social import SocialAdminService
from api_gateway.admin.watchlist import WatchlistAdminService, WatchlistError
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
    return {"sub": principal.sub, "role": principal.role, "auth_mode": principal.auth_mode,
            "local_mode": settings.admin_local_mode,
            "mutations_enabled": settings.admin_operational_actions_enabled,
            "environment": _environment(), "region": _region(), "read_only": True}


# ---- production auth (Google Workspace OIDC) ----------------------------------------------------
def _auth_mode() -> str:
    if settings.admin_local_mode:
        return "local"
    if oidc.is_configured():
        return "oidc"
    return "disabled"


def _environment() -> str:
    """A human-facing environment label so the console never looks like local dev. `production` is a
    real, authenticated cloud deployment; `local` is the developer console."""
    if settings.admin_local_mode:
        return "local"
    if os.environ.get("FLY_APP_NAME") or oidc.is_configured():
        return "production"
    return "unknown"


def _region() -> str:
    return os.environ.get("FLY_REGION") or ("local" if settings.admin_local_mode else "unknown")


@router.get("/auth/status", summary="Public auth status (unauthenticated)")
def auth_status(authorization: str | None = None,
                session_cookie: str | None = Cookie(default=None, alias=settings.session_cookie_name),
                ) -> dict[str, Any]:
    """Unauthenticated: lets the SPA render the right entry (local / sign-in / disabled) without a 401
    round-trip. Never leaks anything beyond whether a valid session is already present."""
    if not settings.admin_api_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="admin api disabled")
    mode = _auth_mode()
    principal = auth.internal_user() if settings.admin_local_mode else auth.authenticate(session_cookie)
    return {"auth_mode": mode, "authenticated": principal is not None,
            "sub": principal.sub if principal else None,
            "login_url": "/admin/v1/auth/login",
            "environment": _environment(), "region": _region(), "read_only": True}


@router.get("/auth/login", summary="Begin Google sign-in")
def auth_login(next: str = Query(default="/")) -> Response:
    if not settings.admin_api_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="admin api disabled")
    if settings.admin_local_mode:
        return RedirectResponse(url=next or "/", status_code=status.HTTP_302_FOUND)
    if not oidc.is_configured():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Google sign-in is not configured on this deployment")
    try:
        url = oidc.build_login_url(next)
    except oidc.OidcError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)


@router.get("/auth/callback", summary="Google OIDC redirect target")
async def auth_callback(code: str | None = Query(default=None), state: str | None = Query(default=None),
                        error: str | None = Query(default=None)) -> Response:
    if not settings.admin_api_enabled or settings.admin_local_mode or not oidc.is_configured():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="not found")
    if error:
        return RedirectResponse(url="/?login_error=access_denied", status_code=status.HTTP_302_FOUND)
    try:
        next_path, nonce = oidc.verify_state(state)
        if not code:
            raise oidc.OidcError("missing authorization code")
        result = await oidc.exchange_code(code, nonce)
    except oidc.OidcError:
        # Do not echo internal detail into the URL; the login screen shows a generic message.
        return RedirectResponse(url="/?login_error=1", status_code=status.HTTP_302_FOUND)
    token = auth.issue_session(result.email, "VIEWER", auth_mode="oidc")
    resp = RedirectResponse(url=next_path or "/", status_code=status.HTTP_302_FOUND)
    resp.set_cookie(settings.session_cookie_name, token, max_age=settings.admin_session_ttl_seconds,
                    httponly=True, secure=settings.session_cookie_secure, samesite="lax", path="/")
    return resp


@router.post("/auth/logout", summary="Clear the session")
def auth_logout() -> Response:
    resp = Response(status_code=status.HTTP_204_NO_CONTENT)
    resp.delete_cookie(settings.session_cookie_name, path="/")
    return resp


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


@router.get("/sources/{source}/diagnostics")
async def source_diagnostics(source: str, _: auth.Principal = Depends(auth.require_viewer),
                             svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.source_diagnostics(source)


def _event_filters(source: str | None, stale_only: bool, has_transitions: bool, q: str | None,
                   city: str | None, date_from: str | None, date_to: str | None,
                   capture_state: str | None, resolution_status: str | None,
                   temporal_state: str | None = None, provider_lifecycle: str | None = None) -> dict[str, Any]:
    return {"source": source, "stale_only": stale_only, "has_transitions": has_transitions,
            "q": q, "city": city, "date_from": date_from, "date_to": date_to,
            "capture_state": capture_state, "resolution_status": resolution_status,
            "temporal_state": temporal_state, "provider_lifecycle": provider_lifecycle}


@router.get("/events")
async def events(source: str | None = Query(default=None),
                 stale_only: bool = Query(default=False),
                 has_transitions: bool = Query(default=False),
                 q: str | None = Query(default=None),
                 city: str | None = Query(default=None),
                 date_from: str | None = Query(default=None),
                 date_to: str | None = Query(default=None),
                 capture_state: str | None = Query(default=None),
                 resolution_status: str | None = Query(default=None),
                 temporal_state: str | None = Query(default=None),
                 provider_lifecycle: str | None = Query(default=None),
                 limit: int = Query(default=25, ge=1, le=100),
                 offset: int = Query(default=0, ge=0),
                 _: auth.Principal = Depends(auth.require_viewer),
                 svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    f = _event_filters(source, stale_only, has_transitions, q, city, date_from, date_to,
                       capture_state, resolution_status, temporal_state, provider_lifecycle)
    return await svc.events(**f, limit=limit, offset=offset)


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
                           status_filter: str | None = Query(default=None, alias="status"),
                           limit: int = Query(default=50, ge=1, le=200),
                           _: auth.Principal = Depends(auth.require_viewer),
                           svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.resolution_queue(entity_type=entity_type, source=source,
                                      status=status_filter, limit=limit)


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


@router.get("/data-quality")
async def data_quality(_: auth.Principal = Depends(auth.require_viewer),
                       svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.data_quality()


@router.get("/data-quality/review-queue")
async def data_quality_review(_: auth.Principal = Depends(auth.require_viewer),
                              svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.review_queue()


@router.get("/data-quality/metrics")
async def data_quality_metrics(_: auth.Principal = Depends(auth.require_viewer),
                               svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.quality_metrics()


# Governed data-quality correction — the one narrow canonical-interpretation write. Authenticated
# (Workspace), flag-gated (reuses the research-config controlled-write capability), records the operator,
# audited. No arbitrary canonical CRUD; kept separate from the read surface.
_DQ_ACTIONS = {"MARK_PLACEHOLDER", "CONFIRM_MULTI_ROLE", "REQUEUE_RESOLUTION", "REJECT_MATCH",
               "CONFIRM_EXISTING_MATCH"}


@router.post("/data-quality/correct")
async def data_quality_correct(payload: dict = Body(...),
                               principal: auth.Principal = Depends(auth.require_viewer),
                               svc: AdminService = Depends(get_admin_service),
                               store=Depends(get_audit_store)) -> dict[str, Any]:
    _require_research_config()
    action = str(payload.get("action", "")).upper()
    if action not in _DQ_ACTIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"action must be one of {sorted(_DQ_ACTIONS)}")
    out = await svc.apply_correction(
        action=action, actor=principal.sub, reason=payload.get("reason"),
        canonical_entity_id=payload.get("canonical_entity_id"), candidate_id=payload.get("candidate_id"))
    store.record(actor_id=principal.sub, actor_role=principal.role, action=f"DATA_QUALITY_{action}",
                 object_type="canonical_entity", request_id=uuid.uuid4().hex,
                 object_id=str(payload.get("canonical_entity_id") or payload.get("candidate_id") or ""),
                 new_value={"new_state": out.get("new_state") or out.get("candidates_quarantined")},
                 reason=payload.get("reason"))
    return out


@router.get("/system-health")
async def system_health(_: auth.Principal = Depends(auth.require_viewer),
                        svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.system_health()


@router.get("/search")
async def search(q: str = Query(...), limit: int = Query(default=20, ge=1, le=100),
                 _: auth.Principal = Depends(auth.require_viewer),
                 svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    return await svc.search(q, limit=limit)


# ---- product catalog: first-class Artists & Venues (Phase 5B.2; canonical registry only) --------
@router.get("/catalog/artists")
async def catalog_artists(limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                          watching: bool = Query(default=False),
                          youtube_verified: bool = Query(default=False),
                          needs_identity: bool = Query(default=False),
                          has_demand: bool = Query(default=False),
                          moving: bool = Query(default=False),
                          has_events: bool = Query(default=False),
                          _: auth.Principal = Depends(auth.require_viewer),
                          svc: CatalogAdminService = Depends(get_catalog_service)) -> dict[str, Any]:
    return await svc.artists(limit=limit, offset=offset, watching=watching,
                             youtube_verified=youtube_verified, needs_identity=needs_identity,
                             has_demand=has_demand, moving=moving, has_events=has_events)


@router.get("/catalog/venues")
async def catalog_venues(limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                         has_events: bool = Query(default=False),
                         _: auth.Principal = Depends(auth.require_viewer),
                         svc: CatalogAdminService = Depends(get_catalog_service)) -> dict[str, Any]:
    return await svc.venues(limit=limit, offset=offset, has_events=has_events)


@router.get("/catalog/venues/{venue_id:path}")
async def catalog_venue_detail(venue_id: str, _: auth.Principal = Depends(auth.require_viewer),
                               svc: CatalogAdminService = Depends(get_catalog_service)) -> dict[str, Any]:
    return await svc.venue_detail(venue_id)


@router.get("/catalog/counts")
async def catalog_counts(_: auth.Principal = Depends(auth.require_viewer),
                         svc: CatalogAdminService = Depends(get_catalog_service)) -> dict[str, Any]:
    return await svc.product_counts()


# ---- demand intelligence (Phase 5A.2; read-only; degrades gracefully) ---------------------------
@router.get("/demand/overview")
async def demand_overview(_: auth.Principal = Depends(auth.require_viewer),
                          svc: DemandAdminService = Depends(get_demand_service)) -> dict[str, Any]:
    return await svc.overview()


@router.get("/demand/summary")
async def demand_summary(_: auth.Principal = Depends(auth.require_viewer),
                         svc: DemandAdminService = Depends(get_demand_service)) -> dict[str, Any]:
    return await svc.summary()


@router.get("/demand/artists/{artist_id:path}/observations")
async def demand_artist_observations(artist_id: str,
                                     provider: str | None = Query(default=None),
                                     metric: str | None = Query(default=None),
                                     limit: int = Query(default=100, ge=1, le=200),
                                     offset: int = Query(default=0, ge=0),
                                     _: auth.Principal = Depends(auth.require_viewer),
                                     svc: DemandAdminService = Depends(get_demand_service)) -> dict[str, Any]:
    return await svc.observations(artist_id, provider=provider, metric=metric, limit=limit, offset=offset)


@router.get("/demand/artists/{artist_id:path}/coverage")
async def demand_artist_coverage(artist_id: str, _: auth.Principal = Depends(auth.require_viewer),
                                 svc: DemandAdminService = Depends(get_demand_service)) -> dict[str, Any]:
    return await svc.coverage(artist_id)


# ---- social evidence (Phase 5C.1; read-only coverage/diagnostics; no raw-source export) ----------
@router.get("/social/overview")
async def social_overview(_: auth.Principal = Depends(auth.require_viewer),
                          svc: SocialAdminService = Depends(get_social_service)) -> dict[str, Any]:
    return await svc.overview()


@router.get("/social/identities")
async def social_identities(canonical_entity_id: str | None = Query(default=None),
                            platform: str | None = Query(default=None),
                            limit: int = Query(default=200, ge=1, le=500),
                            _: auth.Principal = Depends(auth.require_viewer),
                            svc: SocialAdminService = Depends(get_social_service)) -> dict[str, Any]:
    return await svc.identities(canonical_entity_id=canonical_entity_id, platform=platform, limit=limit)


@router.get("/social/mentions")
async def social_mentions(canonical_entity_id: str | None = Query(default=None),
                          platform: str | None = Query(default=None),
                          current_only: bool = Query(default=True),
                          limit: int = Query(default=100, ge=1, le=500),
                          _: auth.Principal = Depends(auth.require_viewer),
                          svc: SocialAdminService = Depends(get_social_service)) -> dict[str, Any]:
    return await svc.mentions(canonical_entity_id=canonical_entity_id, platform=platform,
                              current_only=current_only, limit=limit)


@router.get("/social/mentions/history")
async def social_mention_history(platform: str = Query(...), platform_post_id: str = Query(...),
                                 _: auth.Principal = Depends(auth.require_viewer),
                                 svc: SocialAdminService = Depends(get_social_service)) -> dict[str, Any]:
    return await svc.mention_history(platform=platform, platform_post_id=platform_post_id)


@router.get("/demand/artists/{artist_id:path}/movement")
async def demand_artist_movement(artist_id: str, relationship: str | None = Query(default=None),
                                 _: auth.Principal = Depends(auth.require_viewer),
                                 svc: DemandAdminService = Depends(get_demand_service)) -> dict[str, Any]:
    return await svc.movement(artist_id, relationship=relationship)


@router.get("/market/movement")
async def market_movement(limit: int = Query(default=200, ge=1, le=500),
                          _: auth.Principal = Depends(auth.require_viewer),
                          svc: DemandAdminService = Depends(get_demand_service)) -> dict[str, Any]:
    return await svc.market_movement(limit=limit)


@router.get("/demand/artists/{artist_id:path}")
async def demand_artist(artist_id: str, _: auth.Principal = Depends(auth.require_viewer),
                        svc: DemandAdminService = Depends(get_demand_service)) -> dict[str, Any]:
    return await svc.artist(artist_id)


@router.get("/demand/events/{event_id:path}")
async def demand_event(event_id: str, _: auth.Principal = Depends(auth.require_viewer),
                       svc: DemandAdminService = Depends(get_demand_service)) -> dict[str, Any]:
    return await svc.event_context(event_id)


# ---- bounded export (VIEWER; respects the same filters as the live view) ------------------------
def _flatten(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return str(v)


def _to_csv(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    header: list[str] = []
    for r in rows:
        for k in r:
            if k not in header:
                header.append(k)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_flatten(r.get(k)) for k in header])
    return buf.getvalue()


@router.get("/export/{table}")
async def export(table: str, fmt: str = Query(default="csv", alias="format"),
                 source: str | None = Query(default=None),
                 stale_only: bool = Query(default=False),
                 has_transitions: bool = Query(default=False),
                 q: str | None = Query(default=None), city: str | None = Query(default=None),
                 date_from: str | None = Query(default=None), date_to: str | None = Query(default=None),
                 capture_state: str | None = Query(default=None),
                 resolution_status: str | None = Query(default=None),
                 entity_type: str | None = Query(default=None),
                 status_filter: str | None = Query(default=None, alias="status"),
                 _: auth.Principal = Depends(auth.require_viewer),
                 svc: AdminService = Depends(get_admin_service)) -> Response:
    if table not in AdminService.EXPORTABLE:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail=f"exportable tables: {AdminService.EXPORTABLE}")
    if fmt not in ("csv", "json"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="format must be csv or json")
    filters: dict[str, Any] = {}
    if table == "events":
        filters = _event_filters(source, stale_only, has_transitions, q, city, date_from, date_to,
                                 capture_state, resolution_status)
    elif table in ("entities", "resolution-queue"):
        filters = {"entity_type": entity_type, "source": source, "status": status_filter}
    elif table == "capture-jobs":
        filters = {"status": status_filter, "source": source}
    elif table == "source-diagnostics":
        filters = {"source": source}
    rows = await svc.export_rows(table, filters={k: v for k, v in filters.items() if v not in (None, "")})
    fname = f"nquark-{table}"
    if fmt == "json":
        body = json.dumps({"table": table, "count": len(rows), "rows": rows},
                          ensure_ascii=False, indent=2)
        return Response(content=body, media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.json"'})
    return Response(content=_to_csv(rows), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})


# ---- research configuration (Phase 5B.1) --------------------------------------------------------
# The ONE narrow authenticated WRITE surface: operators create / prioritise / pause / resume artist
# WATCH TARGETS. This is deliberately kept separate from the canonical/admin mutation routes below —
# it never touches canonical entities, observations, graph nodes, provider observations, resolution
# outcomes, or event/historical state. Reads use VIEWER; writes additionally require the research-config
# flag and record the authenticated operator as `created_by`. Gated by ADMIN_RESEARCH_CONFIG_ENABLED.
def _require_research_config() -> None:
    if not settings.admin_research_config_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="research configuration is disabled on this deployment")


def _watchlist_error(exc: WatchlistError):
    return HTTPException(exc.status if exc.status in range(400, 600) else 502, detail=exc.detail)


@router.get("/research/watchlist")
async def watchlist_list(status_filter: str | None = Query(default=None, alias="status"),
                         limit: int = Query(default=100, ge=1, le=500),
                         offset: int = Query(default=0, ge=0),
                         _: auth.Principal = Depends(auth.require_viewer),
                         svc: WatchlistAdminService = Depends(get_watchlist_service)) -> dict[str, Any]:
    return await svc.list_targets(status=status_filter, limit=limit, offset=offset)


@router.get("/research/watchlist/diagnostics")
async def watchlist_diagnostics(_: auth.Principal = Depends(auth.require_viewer),
                                svc: WatchlistAdminService = Depends(get_watchlist_service)) -> dict[str, Any]:
    return await svc.diagnostics()


@router.get("/research/watchlist/canonical-integrity")
async def watchlist_canonical_integrity(_: auth.Principal = Depends(auth.require_viewer),
                                        svc: WatchlistAdminService = Depends(get_watchlist_service)) -> dict[str, Any]:
    return await svc.canonical_integrity()


@router.get("/research/watchlist/{target_id}")
async def watchlist_target(target_id: str, _: auth.Principal = Depends(auth.require_viewer),
                           svc: WatchlistAdminService = Depends(get_watchlist_service)) -> dict[str, Any]:
    try:
        return await svc.get_target(target_id)
    except WatchlistError as exc:
        raise _watchlist_error(exc) from exc


@router.post("/research/watchlist/bulk/preview")
async def watchlist_bulk_preview(payload: dict = Body(...),
                                 _: auth.Principal = Depends(auth.require_viewer),
                                 svc: WatchlistAdminService = Depends(get_watchlist_service)) -> dict[str, Any]:
    # a no-write preview (matches/duplicates) so the operator can review before creating targets.
    try:
        return await svc.bulk_preview(text=payload.get("text"), names=payload.get("names"))
    except WatchlistError as exc:
        raise _watchlist_error(exc) from exc


@router.post("/research/watchlist")
async def watchlist_add(payload: dict = Body(...),
                        principal: auth.Principal = Depends(auth.require_viewer),
                        svc: WatchlistAdminService = Depends(get_watchlist_service),
                        store=Depends(get_audit_store)) -> dict[str, Any]:
    _require_research_config()
    display_name = str(payload.get("display_name", "")).strip()
    if not display_name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="display_name required")
    try:
        out = await svc.add_target(
            created_by=principal.sub, display_name=display_name,
            canonical_artist_id=payload.get("canonical_artist_id"),
            youtube_hint=payload.get("youtube_hint"), reason=payload.get("reason"),
            priority=payload.get("priority"))
    except WatchlistError as exc:
        raise _watchlist_error(exc) from exc
    target = out.get("target") or {}
    store.record(actor_id=principal.sub, actor_role=principal.role, action="WATCHLIST_ADD",
                 object_type="watch_target", object_id=str(target.get("id", "")),
                 request_id=uuid.uuid4().hex,
                 new_value={"display_name": display_name, "status": target.get("status")},
                 reason=payload.get("reason"))
    return out


@router.post("/research/watchlist/bulk")
async def watchlist_bulk_add(payload: dict = Body(...),
                             principal: auth.Principal = Depends(auth.require_viewer),
                             svc: WatchlistAdminService = Depends(get_watchlist_service),
                             store=Depends(get_audit_store)) -> dict[str, Any]:
    _require_research_config()
    try:
        out = await svc.bulk_add(created_by=principal.sub, text=payload.get("text"),
                                 names=payload.get("names"), reason=payload.get("reason"))
    except WatchlistError as exc:
        raise _watchlist_error(exc) from exc
    store.record(actor_id=principal.sub, actor_role=principal.role, action="WATCHLIST_BULK_ADD",
                 object_type="watch_target", object_id="bulk", request_id=uuid.uuid4().hex,
                 new_value={"received": out.get("received"), "created": out.get("created")},
                 reason=payload.get("reason"))
    return out


def _watchlist_action(action: str):
    async def _run(target_id: str, principal: auth.Principal,
                   svc: WatchlistAdminService, store, coro, reason: str | None = None):
        _require_research_config()
        try:
            out = await coro
        except WatchlistError as exc:
            raise _watchlist_error(exc) from exc
        store.record(actor_id=principal.sub, actor_role=principal.role, action=action,
                     object_type="watch_target", object_id=target_id, request_id=uuid.uuid4().hex,
                     new_value={"status": out.get("status")}, reason=reason)
        return out
    return _run


@router.post("/research/watchlist/{target_id}/pause")
async def watchlist_pause(target_id: str,
                          principal: auth.Principal = Depends(auth.require_viewer),
                          svc: WatchlistAdminService = Depends(get_watchlist_service),
                          store=Depends(get_audit_store)) -> dict[str, Any]:
    return await _watchlist_action("WATCHLIST_PAUSE")(target_id, principal, svc, store, svc.pause(target_id))


@router.post("/research/watchlist/{target_id}/resume")
async def watchlist_resume(target_id: str,
                           principal: auth.Principal = Depends(auth.require_viewer),
                           svc: WatchlistAdminService = Depends(get_watchlist_service),
                           store=Depends(get_audit_store)) -> dict[str, Any]:
    return await _watchlist_action("WATCHLIST_RESUME")(target_id, principal, svc, store, svc.resume(target_id))


@router.post("/research/watchlist/{target_id}/priority")
async def watchlist_priority(target_id: str, payload: dict = Body(...),
                             principal: auth.Principal = Depends(auth.require_viewer),
                             svc: WatchlistAdminService = Depends(get_watchlist_service),
                             store=Depends(get_audit_store)) -> dict[str, Any]:
    try:
        priority = int(payload.get("priority"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="priority (int) required") from exc
    return await _watchlist_action("WATCHLIST_PRIORITY")(
        target_id, principal, svc, store, svc.set_priority(target_id, priority))


@router.post("/research/watchlist/{target_id}/reject")
async def watchlist_reject(target_id: str, payload: dict = Body(default={}),
                           principal: auth.Principal = Depends(auth.require_viewer),
                           svc: WatchlistAdminService = Depends(get_watchlist_service),
                           store=Depends(get_audit_store)) -> dict[str, Any]:
    reason = payload.get("reason") if isinstance(payload, dict) else None
    return await _watchlist_action("WATCHLIST_REJECT")(
        target_id, principal, svc, store, svc.reject(target_id, reason), reason=reason)


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
