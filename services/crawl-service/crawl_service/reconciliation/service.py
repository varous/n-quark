"""ReconciliationService (Phase 3) — generate bounded cross-platform match candidates, score them
deterministically, link accepted matches without collapsing source truth, and reconcile fields across
independence groups.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from crawl_service.config import Settings, settings
from crawl_service.enrichment.clients import GraphReader
from crawl_service.enrichment.resolver import resolve_field
from crawl_service.enrichment.service import EnrichmentService
from crawl_service.models import EventMatchCandidate, TrackedEvent
from crawl_service.reconciliation import matcher as M
from crawl_service.reconciliation.views import EventView

_RECONCILE_FIELDS = ("starts_at", "end_at", "venue_name", "venue_id", "city", "region_id",
                     "event_status", "organizer")


def _uuid() -> str:
    return uuid.uuid4().hex


def _iso(dt):
    return dt.isoformat() if dt else None


def _pair_key(a: str, b: str) -> str:
    return "::".join(sorted([a, b]))


class ReconciliationService:
    def __init__(self, session_factory, graph_reader: GraphReader, enricher: EnrichmentService,
                 config: Settings | None = None) -> None:
        self._sf = session_factory
        self._graph = graph_reader
        self._enricher = enricher
        self._cfg = config or settings

    # ---- event views -----------------------------------------------------------------------------
    async def _view(self, te: TrackedEvent) -> EventView:
        node, neighbors = await self._graph.get_event(te.canonical_event_id)
        return EventView.from_graph(source=te.source, source_record_id=te.source_record_id,
                                    node=node, neighbors=neighbors)

    def _tracked(self, source: str) -> list[TrackedEvent]:
        with self._sf() as s:
            return s.execute(
                select(TrackedEvent).where(
                    TrackedEvent.source == source, TrackedEvent.canonical_event_id.is_not(None))
            ).scalars().all()

    # ---- candidate generation + scoring ----------------------------------------------------------
    async def run(self, *, left_source: str = "boshow", right_source: str | None = None,
                  now: datetime | None = None, trace: bool = False) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        right_source = right_source or self._cfg.second_source_name
        left = [await self._view(te) for te in self._tracked(left_source)]
        right = [await self._view(te) for te in self._tracked(right_source)]

        m = {"left_events": len(left), "right_events": len(right), "pairs_evaluated": 0,
             "blocked": 0, "auto_match": 0, "possible_match": 0, "conflict": 0, "not_matched": 0}
        traces: list[dict] = []
        pairs = 0
        for lv in left:
            for rv in right:
                if pairs >= self._cfg.reconciliation_max_pairs_per_run:
                    break
                blocked, block_reason = M.in_block(
                    lv, rv, date_tolerance_hours=self._cfg.reconciliation_date_tolerance_hours)
                if not blocked:
                    continue
                m["blocked"] += 1
                pairs += 1
                m["pairs_evaluated"] += 1
                res = M.score_match(
                    lv, rv, date_tolerance_hours=self._cfg.reconciliation_date_tolerance_hours,
                    auto_threshold=self._cfg.reconciliation_auto_match_threshold,
                    possible_threshold=self._cfg.reconciliation_possible_match_threshold)
                self._persist_candidate(lv, rv, res, now)
                key = {M.AUTO_MATCH: "auto_match", M.POSSIBLE_MATCH: "possible_match",
                       M.CONFLICT: "conflict", M.NOT_MATCHED: "not_matched"}[res.status]
                m[key] += 1
                if trace:
                    traces.append({
                        "left": f"{lv.source}:{lv.source_record_id}",
                        "right": f"{rv.source}:{rv.source_record_id}",
                        "block": block_reason, "status": res.status, "score": res.score,
                        "components": res.components, "supporting": res.supporting,
                        "contradicting": res.contradicting, "reason": res.reason_code,
                        "steps": ["source_events_selected", "block_matched", "fields_normalized",
                                  "component_scores_computed", "contradictions_evaluated",
                                  "match_outcome_assigned"],
                    })
            if pairs >= self._cfg.reconciliation_max_pairs_per_run:
                break

        m["match_rate"] = round(m["auto_match"] / m["pairs_evaluated"], 4) if m["pairs_evaluated"] else 0.0
        result = {"left_source": left_source, "right_source": right_source, "metrics": m}
        if trace:
            result["pairs"] = traces
        return result

    def _persist_candidate(self, lv: EventView, rv: EventView, res: M.MatchResult, now) -> None:
        lk, rk = lv.canonical_event_id or "", rv.canonical_event_id or ""
        pk = _pair_key(f"{lv.source}:{lv.source_record_id}", f"{rv.source}:{rv.source_record_id}")
        with self._sf() as s, s.begin():
            existing = s.execute(
                select(EventMatchCandidate).where(EventMatchCandidate.pair_key == pk)
            ).scalar_one_or_none()
            if existing is None:
                s.add(EventMatchCandidate(
                    id=_uuid(), pair_key=pk,
                    left_source=lv.source, left_source_record_id=lv.source_record_id,
                    left_canonical_event_id=lk or None,
                    right_source=rv.source, right_source_record_id=rv.source_record_id,
                    right_canonical_event_id=rk or None,
                    match_status=res.status, match_score=res.score, component_scores=res.components,
                    supporting_signals=res.supporting, contradicting_signals=res.contradicting,
                    reason_code=res.reason_code, matcher_version=M.MATCHER_VERSION,
                    review_status="AUTO", created_at=now, updated_at=now))
            else:
                existing.match_status = res.status
                existing.match_score = res.score
                existing.component_scores = res.components
                existing.supporting_signals = res.supporting
                existing.contradicting_signals = res.contradicting
                existing.reason_code = res.reason_code
                existing.updated_at = now

    # ---- field reconciliation across a matched pair ----------------------------------------------
    async def reconcile_pair(self, left_event_id: str, right_event_id: str) -> dict[str, Any]:
        left_c = self._enricher.active_candidates(left_event_id)
        right_c = self._enricher.active_candidates(right_event_id)
        fields = {f for f in (set(left_c) | set(right_c)) if f in _RECONCILE_FIELDS}
        resolved: dict[str, Any] = {}
        consensus = conflict = filled = 0
        for f in sorted(fields):
            combined = left_c.get(f, []) + right_c.get(f, [])
            r = resolve_field(f, combined, min_confidence=self._cfg.capture_enrichment_min_confidence)
            resolved[f] = {
                "value": r.resolved_value, "method": r.resolution_method, "confidence": r.confidence,
                "review_status": r.review_status, "reason": r.reason_code,
                "independence_groups": sorted({c.independence_group for c in combined}),
                "left_only": bool(left_c.get(f) and not right_c.get(f)),
                "right_only": bool(right_c.get(f) and not left_c.get(f)),
            }
            if r.reason_code == "RESOLVED_CONSENSUS":
                consensus += 1
            if r.review_status == "CONFLICT":
                conflict += 1
            if resolved[f]["left_only"] or resolved[f]["right_only"]:
                filled += 1
        return {"left_event_id": left_event_id, "right_event_id": right_event_id,
                "fields": resolved,
                "summary": {"consensus": consensus, "conflict": conflict, "single_source_fill": filled}}

    async def source_price_availability(self, left_event_id, right_event_id) -> dict[str, Any]:
        lv = await self._view_by_id(left_event_id)
        rv = await self._view_by_id(right_event_id)

        def _cmp(a, b) -> str:
            if a is None or b is None:
                return "SINGLE_SOURCE"
            return "SAME" if str(a) == str(b) else "PLATFORM_DIFFERENCE"

        return {
            "source_price_comparison": {
                "left": {"source": lv.source, "price_min": lv.price_min, "currency": lv.currency},
                "right": {"source": rv.source, "price_min": rv.price_min, "currency": rv.currency},
                "classification": _cmp(lv.price_min, rv.price_min)},
            "source_availability_comparison": {
                "left": {"source": lv.source, "availability": lv.availability},
                "right": {"source": rv.source, "availability": rv.availability},
                "classification": _cmp(lv.availability, rv.availability)},
        }

    async def _view_by_id(self, event_id: str) -> EventView:
        node, neighbors = await self._graph.get_event(event_id)
        src = (event_id.split(":", 1)[0] if ":" in event_id else "unknown")
        return EventView.from_graph(source=src, source_record_id=event_id, node=node, neighbors=neighbors)

    # ---- reads -----------------------------------------------------------------------------------
    def source_records(self, event_id: str) -> dict[str, Any]:
        """All source listings linked to an event via accepted matches (truth NOT collapsed)."""
        with self._sf() as s:
            rows = s.execute(
                select(EventMatchCandidate).where(
                    EventMatchCandidate.match_status == "MATCHED",
                    (EventMatchCandidate.left_canonical_event_id == event_id)
                    | (EventMatchCandidate.right_canonical_event_id == event_id))
            ).scalars().all()
        records = [{"source": event_id.split(":", 1)[0] if ":" in event_id else "unknown",
                    "canonical_event_id": event_id, "self": True}]
        for r in rows:
            other = (r.right_source, r.right_source_record_id, r.right_canonical_event_id) \
                if r.left_canonical_event_id == event_id \
                else (r.left_source, r.left_source_record_id, r.left_canonical_event_id)
            records.append({"source": other[0], "source_record_id": other[1],
                            "canonical_event_id": other[2], "via_match": r.id, "self": False})
        return {"canonical_event_id": event_id, "represented_by": records}
