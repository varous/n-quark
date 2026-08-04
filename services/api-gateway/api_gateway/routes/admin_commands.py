"""Admin Phase B — governed resolution-decision command endpoints (`/admin/v1/resolution-decisions/...`).

Each mutation is: role-authorized (server-side) → idempotency-checked → executed via crawl's owned
command surface → recorded as an append-only decision + an audit row → returns refreshed state. Original
source evidence is never deleted. Concurrency/linkage conflicts propagate as 409 with an explicit code.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from api_gateway.admin import auth
from api_gateway.admin.decisions import DecisionStore
from api_gateway.admin.deps import get_admin_service, get_audit_store, get_decision_store
from api_gateway.admin.service import AdminService
from api_gateway.config import settings

router = APIRouter(prefix="/admin/v1/resolution-decisions", tags=["admin-governance"])

# reason-required actions (an explicit reason must accompany the command)
_REASON_REQUIRED = {"CREATE_CANONICAL_ENTITY", "MARK_ALIAS", "SUPERSEDE_LEGACY_PROJECTION",
                    "CORRECT_EVENT_SERIES", "REVERSE_DECISION"}


def _require_ops() -> None:
    if not settings.admin_operational_actions_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="admin operations disabled")


def _idem_key(action: str, payload: dict) -> str:
    if payload.get("idempotency_key"):
        return str(payload["idempotency_key"])
    basis = "|".join(str(payload.get(k, "")) for k in
                     ("candidate_id", "event_id", "legacy_entity_id", "canonical_entity_id",
                      "series_id", "decision_id"))
    return f"{action}:{hashlib.sha256(basis.encode()).hexdigest()[:24]}"


def _conflict_from(down) -> None:
    """Propagate a crawl governance conflict/validation error to the caller."""
    if down.status == 409:
        detail = (down.data or {}).get("detail", down.data)
        raise HTTPException(status.HTTP_409_CONFLICT, detail=detail)
    if down.status == 400:
        detail = (down.data or {}).get("detail", down.data)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=detail)
    if not down.ok:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                            detail={"downstream_status": down.status, "error": down.error})


async def _execute(*, action: str, crawl_path: str, crawl_payload: dict, decision_fields: dict,
                   principal: auth.Principal, payload: dict, svc: AdminService,
                   store, decisions: DecisionStore) -> dict[str, Any]:
    _require_ops()
    if action in _REASON_REQUIRED and not str(payload.get("reason", "")).strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"{action} requires an explicit reason")
    idem = _idem_key(action, payload)
    existing = decisions.find_by_idempotency(idem)
    if existing is not None:
        return {"decision": existing, "already_applied": True, "code": "DECISION_ALREADY_APPLIED"}

    down = await svc.governance(crawl_path, crawl_payload)
    _conflict_from(down)
    result = down.data or {}
    request_id = uuid.uuid4().hex
    cand = result.get("candidate", {})
    decision = decisions.create(
        idempotency_key=idem, action=action, actor_id=principal.sub, actor_role=principal.role,
        request_id=request_id, reason_code=payload.get("reason_code"),
        note=payload.get("reason") or payload.get("note"),
        impact_snapshot=payload.get("impact_snapshot"), result=result,
        previous_status=result.get("previous_status"),
        new_status=cand.get("status"),
        previous_canonical_entity_id=result.get("previous_canonical_entity_id"),
        selected_canonical_entity_id=(crawl_payload.get("canonical_entity_id")
                                      or result.get("created_canonical_entity_id")),
        created_canonical_entity_id=result.get("created_canonical_entity_id"),
        **decision_fields)
    store.record(actor_id=principal.sub, actor_role=principal.role, action=action,
                 object_type="candidate" if cand else "entity",
                 object_id=str(decision_fields.get("candidate_id")
                               or crawl_payload.get("legacy_entity_id")
                               or crawl_payload.get("event_id") or ""),
                 request_id=request_id, new_value={"decision_id": decision["id"], "result": result},
                 reason=payload.get("reason"))
    return {"decision": decision, "result": result, "request_id": request_id}


# ---- preview (no mutation) ----------------------------------------------------------------------
@router.post("/preview")
async def preview(payload: dict = Body(...), _: auth.Principal = Depends(auth.require_analyst),
                  svc: AdminService = Depends(get_admin_service)) -> dict[str, Any]:
    down = await svc.governance("preview", payload)
    _conflict_from(down)
    return down.data or {}


# ---- ANALYST commands ---------------------------------------------------------------------------
@router.post("/accept")
async def accept(payload: dict = Body(...), principal: auth.Principal = Depends(auth.require_analyst),
                 svc: AdminService = Depends(get_admin_service), store=Depends(get_audit_store),
                 decisions: DecisionStore = Depends(get_decision_store)) -> dict[str, Any]:
    cp = {k: payload[k] for k in ("candidate_id", "canonical_entity_id", "expected_status") if k in payload}
    return await _execute(action="ACCEPT_CANDIDATE", crawl_path="accept", crawl_payload=cp,
                          decision_fields={"candidate_id": payload.get("candidate_id"),
                                           "entity_type": payload.get("entity_type")},
                          principal=principal, payload=payload, svc=svc, store=store, decisions=decisions)


@router.post("/reject")
async def reject(payload: dict = Body(...), principal: auth.Principal = Depends(auth.require_analyst),
                 svc: AdminService = Depends(get_admin_service), store=Depends(get_audit_store),
                 decisions: DecisionStore = Depends(get_decision_store)) -> dict[str, Any]:
    if not str(payload.get("reason_code", "")).strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="reason_code required")
    cp = {k: payload[k] for k in ("candidate_id", "reason_code", "expected_status") if k in payload}
    payload = {**payload, "reason_code": payload.get("reason_code")}
    return await _execute(action="REJECT_CANDIDATE", crawl_path="reject", crawl_payload=cp,
                          decision_fields={"candidate_id": payload.get("candidate_id")},
                          principal=principal, payload=payload,
                          svc=svc, store=store, decisions=decisions)


@router.post("/create-entity")
async def create_entity(payload: dict = Body(...), principal: auth.Principal = Depends(auth.require_analyst),
                        svc: AdminService = Depends(get_admin_service), store=Depends(get_audit_store),
                        decisions: DecisionStore = Depends(get_decision_store)) -> dict[str, Any]:
    cp = {k: payload[k] for k in ("entity_type", "canonical_name", "candidate_id", "city",
                                  "region_id", "organizer") if k in payload}
    return await _execute(action="CREATE_CANONICAL_ENTITY", crawl_path="create-entity", crawl_payload=cp,
                          decision_fields={"candidate_id": payload.get("candidate_id"),
                                           "entity_type": payload.get("entity_type")},
                          principal=principal, payload=payload, svc=svc, store=store, decisions=decisions)


@router.post("/link-handle")
async def link_handle(payload: dict = Body(...), principal: auth.Principal = Depends(auth.require_analyst),
                      svc: AdminService = Depends(get_admin_service), store=Depends(get_audit_store),
                      decisions: DecisionStore = Depends(get_decision_store)) -> dict[str, Any]:
    cp = {k: payload[k] for k in ("candidate_id", "canonical_entity_id", "expected_status") if k in payload}
    return await _execute(action="LINK_SOURCE_HANDLE", crawl_path="link-handle", crawl_payload=cp,
                          decision_fields={"candidate_id": payload.get("candidate_id")},
                          principal=principal, payload=payload, svc=svc, store=store, decisions=decisions)


@router.post("/mark-alias")
async def mark_alias(payload: dict = Body(...), principal: auth.Principal = Depends(auth.require_analyst),
                     svc: AdminService = Depends(get_admin_service), store=Depends(get_audit_store),
                     decisions: DecisionStore = Depends(get_decision_store)) -> dict[str, Any]:
    cp = {k: payload[k] for k in ("candidate_id", "canonical_entity_id", "expected_status") if k in payload}
    return await _execute(action="MARK_ALIAS", crawl_path="link-handle", crawl_payload=cp,
                          decision_fields={"candidate_id": payload.get("candidate_id")},
                          principal=principal, payload=payload, svc=svc, store=store, decisions=decisions)


@router.post("/mark-unresolved")
async def mark_unresolved(payload: dict = Body(...), principal: auth.Principal = Depends(auth.require_analyst),
                          svc: AdminService = Depends(get_admin_service), store=Depends(get_audit_store),
                          decisions: DecisionStore = Depends(get_decision_store)) -> dict[str, Any]:
    cp = {k: payload[k] for k in ("candidate_id", "reason_code") if k in payload}
    return await _execute(action="MARK_UNRESOLVED", crawl_path="mark-unresolved", crawl_payload=cp,
                          decision_fields={"candidate_id": payload.get("candidate_id")},
                          principal=principal, payload=payload, svc=svc, store=store, decisions=decisions)


@router.post("/correct-series")
async def correct_series(payload: dict = Body(...), principal: auth.Principal = Depends(auth.require_analyst),
                         svc: AdminService = Depends(get_admin_service), store=Depends(get_audit_store),
                         decisions: DecisionStore = Depends(get_decision_store)) -> dict[str, Any]:
    cp = {k: payload[k] for k in ("event_id", "mode", "series_id", "series_name", "organizer",
                                  "prev_series_id") if k in payload}
    return await _execute(action="CORRECT_EVENT_SERIES", crawl_path="correct-series", crawl_payload=cp,
                          decision_fields={}, principal=principal, payload=payload, svc=svc,
                          store=store, decisions=decisions)


# ---- ADMIN commands -----------------------------------------------------------------------------
@router.post("/supersede-legacy")
async def supersede_legacy(payload: dict = Body(...), principal: auth.Principal = Depends(auth.require_admin),
                           svc: AdminService = Depends(get_admin_service), store=Depends(get_audit_store),
                           decisions: DecisionStore = Depends(get_decision_store)) -> dict[str, Any]:
    cp = {k: payload[k] for k in ("entity_type", "legacy_entity_id", "canonical_entity_id") if k in payload}
    return await _execute(action="SUPERSEDE_LEGACY_PROJECTION", crawl_path="supersede-legacy",
                          crawl_payload=cp, decision_fields={}, principal=principal, payload=payload,
                          svc=svc, store=store, decisions=decisions)


@router.post("/{decision_id}/reverse")
async def reverse(decision_id: str, payload: dict = Body(default={}),
                  principal: auth.Principal = Depends(auth.require_admin),
                  svc: AdminService = Depends(get_admin_service), store=Depends(get_audit_store),
                  decisions: DecisionStore = Depends(get_decision_store)) -> dict[str, Any]:
    _require_ops()
    if not str(payload.get("reason", "")).strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="REVERSE_DECISION requires a reason")
    original = decisions.get(decision_id)
    if original is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="decision not found")
    if original.get("reversed"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="DECISION_ALREADY_REVERSED")
    deps = decisions.dependents(decision_id)
    if deps:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail={"code": "REVERSAL_REQUIRES_MANUAL_DEPENDENCY_RESOLUTION",
                                    "dependents": deps})
    request_id = uuid.uuid4().hex
    result: dict[str, Any] = {"reversed_decision": decision_id, "action": original["action"]}
    if original["action"] in ("ACCEPT_CANDIDATE", "LINK_SOURCE_HANDLE", "MARK_ALIAS",
                              "CREATE_CANONICAL_ENTITY") and original.get("candidate_id"):
        cp = {"candidate_id": original["candidate_id"],
              "restore_status": original.get("previous_status"),
              "restore_canonical": original.get("previous_canonical_entity_id"),
              "remove_handle": original.get("action") == "CREATE_CANONICAL_ENTITY"
                               or not original.get("previous_canonical_entity_id")}
        down = await svc.governance("reverse-accept", cp)
        _conflict_from(down)
        result["crawl"] = down.data
    else:
        result["note"] = "no automated data reversal for this action type; decision marked reversed"
    reversing = decisions.create(
        idempotency_key=f"REVERSE:{decision_id}", action="REVERSE_DECISION",
        actor_id=principal.sub, actor_role=principal.role, request_id=request_id,
        note=payload.get("reason"), supersedes_decision_id=decision_id, result=result,
        candidate_id=original.get("candidate_id"))
    decisions.mark_reversed(decision_id, by_actor=principal.sub, by_decision_id=reversing["id"])
    store.record(actor_id=principal.sub, actor_role=principal.role, action="REVERSE_DECISION",
                 object_type="decision", object_id=decision_id, request_id=request_id,
                 previous_value=original, new_value=result, reason=payload.get("reason"))
    return {"decision": reversing, "reversed": decision_id, "result": result}


# ---- reads (VIEWER) -----------------------------------------------------------------------------
@router.get("")
def list_decisions(action: str | None = Query(default=None),
                   candidate_id: str | None = Query(default=None),
                   entity_id: str | None = Query(default=None),
                   limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
                   _: auth.Principal = Depends(auth.require_viewer),
                   decisions: DecisionStore = Depends(get_decision_store)) -> dict[str, Any]:
    return decisions.list(action=action, candidate_id=candidate_id, entity_id=entity_id,
                          limit=limit, offset=offset)


@router.get("/{decision_id}")
def get_decision(decision_id: str, _: auth.Principal = Depends(auth.require_viewer),
                 decisions: DecisionStore = Depends(get_decision_store)) -> dict[str, Any]:
    d = decisions.get(decision_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="decision not found")
    return d
