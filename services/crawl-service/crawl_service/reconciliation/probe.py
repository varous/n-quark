"""Source-selection probe (Phase 3) — compare candidate second sources on a small live cohort and
recommend one deterministically. Uses signal-service discover/preview over HTTP (injectable)."""

from __future__ import annotations

from typing import Any

import httpx

from crawl_service.config import settings

_FIELDS = ("starts_at", "venue", "city", "region", "price_min", "artists")


def _rate(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


class ProbeService:
    def __init__(self, signal_url: str | None = None, discover=None, preview=None) -> None:
        self._signal = (signal_url or settings.signal_service_url).rstrip("/")
        self._discover = discover or self._http_discover
        self._preview = preview or self._http_preview

    async def _http_discover(self, source: str, limit: int) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{self._signal}/v1/signals/ticketing/discover",
                            params={"source": source, "limit": limit})
            return {"ok": r.status_code == 200, "refs": r.json().get("event_refs", []) if r.status_code == 200 else []}

    async def _http_preview(self, source: str, ref: str) -> dict:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{self._signal}/v1/signals/ticketing/events/{ref}/preview",
                            params={"source": source})
            return {"status": r.status_code, "body": r.json() if r.status_code == 200 else {}}

    async def probe_source(self, source: str, *, sample: int) -> dict[str, Any]:
        disc = await self._discover(source, sample)
        refs = disc["refs"][:sample]
        attempted = retrieved = 0
        field_hits = dict.fromkeys(_FIELDS, 0)
        for ref in refs:
            attempted += 1
            try:
                pv = await self._preview(source, ref)
            except Exception:  # noqa: BLE001,S112 — one bad preview shouldn't abort the probe
                continue
            if pv.get("status") != 200:
                continue
            retrieved += 1
            body = pv.get("body", {})
            for f in _FIELDS:
                v = body.get(f)
                if v not in (None, "", []):
                    field_hits[f] += 1
        coverage = {f: _rate(field_hits[f], retrieved) for f in _FIELDS}
        structured = round(sum(coverage.values()) / len(_FIELDS), 4)
        return {
            "source": source, "discover_ok": disc["ok"], "event_volume": len(disc["refs"]),
            "pages_attempted": attempted, "pages_retrieved": retrieved,
            "retrieval_success_rate": _rate(retrieved, attempted),
            "field_coverage": coverage, "structured_metadata_score": structured,
        }

    async def compare(self, sources: list[str], *, sample: int = 10) -> dict[str, Any]:
        results = {s: await self.probe_source(s, sample=sample) for s in sources}
        viable = {s: r for s, r in results.items()
                  if r["discover_ok"] and r["retrieval_success_rate"] >= 0.5}
        if not viable:
            return {"results": results, "recommendation": None,
                    "reason": "no candidate source retrieved viably"}
        best = max(viable, key=lambda s: viable[s]["structured_metadata_score"])
        return {
            "results": results, "recommendation": best,
            "reason": f"{best} has the highest structured-metadata score "
                      f"({viable[best]['structured_metadata_score']}) with viable retrieval",
        }
