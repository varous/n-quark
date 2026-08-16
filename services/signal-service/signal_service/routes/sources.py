"""Internal per-source ticketing diagnostics (Phase 4C).

Bounded, no raw full-page HTML. Discovery-time quality only (accepted/rejected, rejection reasons,
field present/valid/specific). Capture-time metrics live in the capture pipeline (crawl + admin BFF).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from signal_service.adapters import sources as src
from signal_service.config import settings

router = APIRouter(prefix="/v1/internal/sources", tags=["sources (internal)"])


@router.get("/descriptors", summary="Governed supply-source descriptors")
def descriptors() -> dict[str, object]:
    rows = src.source_descriptors()
    return {"count": len(rows), "sources": rows}


def _require_managed(source: str) -> None:
    if source not in src.managed_sources():
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail=f"unmanaged source; known: {src.managed_sources()}")


@router.get("", summary="List managed ticketing sources + enabled flags")
def list_sources() -> dict[str, Any]:
    out = []
    for s in src.managed_sources():
        out.append({"source": s, "enabled": src.source_enabled(s),
                    "capabilities": ["discover", "fetch_event", "normalize_event", "classify_failure",
                                     "extract_source_handles", "extract_asset_references"]})
    return {"sources": out, "note": "Observed ticketing supply only — not total-market coverage."}


@router.get("/{source}/quality", summary="Discovery-time quality report (bounded validated pass)")
async def quality(source: str, city: str | None = Query(default=None),
                  limit: int = Query(default=10, ge=1, le=25)) -> dict[str, Any]:
    _require_managed(source)
    report = await src.validated_discovery(source, city=city, limit=limit)
    return src.quality_report(report)


@router.get("/{source}/rejections", summary="Sampled rejected records + reasons")
async def rejections(source: str, city: str | None = Query(default=None),
                     rejection_reason: str | None = Query(default=None),
                     limit: int = Query(default=20, ge=1, le=50),
                     offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    _require_managed(source)
    report = await src.validated_discovery(source, city=city, limit=limit)
    items = [{"event_ref": r.event_ref, "reasons": r.reasons, "source_url": r.source_url,
              "fetch_error": r.fetch_error} for r in report.rejected]
    if rejection_reason:
        items = [it for it in items if rejection_reason in it["reasons"]]
    return {"source": source, "available": report.available, "count": len(items),
            "by_reason": report.rejections_by_reason(),
            "items": items[offset:offset + limit]}


@router.get("/{source}/coverage", summary="Discovery + field coverage")
async def coverage(source: str, city: str | None = Query(default=None),
                   limit: int = Query(default=10, ge=1, le=25)) -> dict[str, Any]:
    _require_managed(source)
    report = await src.validated_discovery(source, city=city, limit=limit)
    q = src.quality_report(report)
    return {"source": source, "available": report.available,
            "records_discovered": q["records_discovered"], "records_accepted": q["records_accepted"],
            "records_rejected": q["records_rejected"], "records_out_of_scope": q["records_out_of_scope"],
            "field_quality": q["field_quality"], "note": q["note"]}


@router.get("/{source}/sample", summary="Sample of accepted normalized events (no raw HTML)")
async def sample(source: str, city: str | None = Query(default=None),
                 limit: int = Query(default=5, ge=1, le=20)) -> dict[str, Any]:
    _require_managed(source)
    report = await src.validated_discovery(source, city=city, limit=limit)
    return {"source": source, "available": report.available, "city_filter": city,
            "accepted": [vars(a) for a in report.accepted]}


@router.get("/{source}/validated-discovery", summary="Accepted refs for enrollment (Kolkata-first)")
async def validated_discovery(source: str, city: str | None = Query(default=None),
                              limit: int = Query(default=None)) -> dict[str, Any]:
    _require_managed(source)
    if source == "skillbox" and not settings.skillbox_discovery_enabled:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="skillbox discovery disabled")
    lim = limit or (settings.skillbox_discovery_limit if source == "skillbox" else 20)
    report = await src.validated_discovery(source, city=city, limit=lim)
    return {"source": source, "available": report.available, "error": report.error,
            "accepted_refs": [a.event_ref for a in report.accepted],
            "accepted_count": len(report.accepted), "rejected_count": len(report.rejected),
            "out_of_scope_count": len(report.out_of_scope)}
