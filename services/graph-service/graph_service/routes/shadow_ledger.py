"""Internal Shadow Ledger surface (Phase 1).

NOT part of the public `/v1/events` feed (ADR-0002). Exposes, under `/v1/internal`, the ability to
record an observed commercial state (`observe`) and to inspect an event's full state + transition
history with an auditable `?trace=true` evidence chain.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status

from graph_service.config import settings
from graph_service.deps import get_shadow_store
from graph_service.schemas import (
    ShadowLedgerResponse,
    ShadowObserveRequest,
    ShadowObserveResponse,
)
from graph_service.shadow_ledger import DETECTOR_VERSION, FIELD_SPECS

router = APIRouter(prefix="/v1/internal/events", tags=["shadow-ledger (internal)"])


def _raw_state(payload: ShadowObserveRequest) -> dict:
    return {spec.source_field: getattr(payload, spec.source_field) for spec in FIELD_SPECS}


@router.post(
    "/{event_id}/shadow-ledger/observe",
    response_model=ShadowObserveResponse,
    summary="Record an observed commercial state; detect + persist transitions (internal)",
)
def observe_state(
    event_id: str,
    payload: ShadowObserveRequest,
    trace: bool = Query(default=False, description="Return the detection evidence chain."),
    store=Depends(get_shadow_store),
) -> ShadowObserveResponse:
    if not settings.shadow_ledger_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Shadow Ledger is disabled"
        )
    observed_at = None
    if payload.observed_at:
        try:
            observed_at = datetime.fromisoformat(payload.observed_at)  # handles trailing 'Z' (py3.11+)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"invalid observed_at: {exc}",
            ) from exc

    result = store.observe(
        canonical_event_id=event_id,
        source_id=payload.source_id,
        source_record_id=payload.source_record_id,
        observation_id=payload.observation_id,
        observed_at=observed_at,
        provenance=payload.provenance,
        epistemic_status=payload.epistemic_status,
        present=payload.present,
        absence_reason=payload.absence_reason,
        raw_state=_raw_state(payload),
        disappearance_threshold=settings.shadow_ledger_disappearance_threshold,
    )
    return ShadowObserveResponse(
        canonical_event_id=event_id,
        noop=result["noop"],
        state=result["state"],
        transitions=result["transitions"],
        trace=result["trace"] if trace else None,
    )


@router.get(
    "/{event_id}/shadow-ledger",
    response_model=ShadowLedgerResponse,
    summary="Retrieve an event's commercial-state + transition history (internal)",
)
def get_shadow_ledger(
    event_id: str,
    source: str | None = Query(default=None, description="Filter to one source_id"),
    trace: bool = Query(default=False, description="Include the per-transition evidence chain."),
    limit: int = Query(default=200, ge=1, le=1000),
    store=Depends(get_shadow_store),
) -> ShadowLedgerResponse:
    states = store.list_states(event_id, source_id=source, limit=limit)
    transitions = store.list_transitions(event_id, source_id=source, limit=limit)
    current = states[-1] if states else None

    trace_payload = None
    if trace:
        by_id = {s["id"]: s for s in states}
        chain = []
        for t in transitions:
            frm = by_id.get(t["from_state_id"]) if t["from_state_id"] else None
            to = by_id.get(t["to_state_id"])
            chain.append(
                {
                    "step": t["transition_type"],
                    "field": t["field_name"],
                    "source_payload_ref": (to or {}).get("provenance", {}).get("source_url")
                    or (to or {}).get("source_record_id"),
                    "observation": (to or {}).get("observation_id"),
                    "canonical_event_id": event_id,
                    "normalized_commercial_state": (to or {}).get("normalized_state"),
                    "previous_state_lookup": {
                        "previous_state_id": t["from_state_id"],
                        "previous_state_hash": (frm or {}).get("state_hash"),
                    },
                    "comparison": {
                        "previous_value": t["previous_value"],
                        "current_value": t["current_value"],
                        "new_state_hash": (to or {}).get("state_hash"),
                    },
                    "emitted_transition": {
                        "type": t["transition_type"],
                        "confidence": t["confidence"],
                        "epistemic_status": t["epistemic_status"],
                    },
                }
            )
        trace_payload = {"detector_version": DETECTOR_VERSION, "evidence_chain": chain}

    return ShadowLedgerResponse(
        canonical_event_id=event_id,
        source=source,
        detector_version=DETECTOR_VERSION,
        current_state=current,
        states=states,
        transitions=transitions,
        trace=trace_payload,
    )
