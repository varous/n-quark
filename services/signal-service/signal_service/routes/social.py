"""Governed social acquisition endpoints (Phase 5C.1).

Internal only. Returns extracted claims + provenance (never raw captions/media), or an honest access
state when authorized access is absent. The crawl-service watchlist scheduler calls ``/collect``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body

from signal_service.adapters import social as S

router = APIRouter(prefix="/v1/signals/social", tags=["social (internal)"])


@router.get("/descriptors", summary="Governed social source descriptors + honest access state")
def descriptors() -> dict[str, Any]:
    rows = S.descriptors()
    return {"count": len(rows), "sources": rows,
            "note": "Acquisition posture (access_state) is separate from transformation posture. "
                    "No scraping fallback; absent authorized access is reported, not worked around."}


@router.get("/access", summary="Access state per platform")
def access() -> dict[str, Any]:
    return {"platforms": {p: S.access_state(p) for p in ("instagram", "facebook", "reddit")}}


@router.post("/collect", summary="Collect durable social claims for one watched public account")
async def collect(payload: dict = Body(...)) -> dict[str, Any]:
    result = await S.collect(
        str(payload.get("platform", "")), account=str(payload.get("account", "")),
        evidence_role=payload.get("evidence_role"))
    return result.to_dict()
