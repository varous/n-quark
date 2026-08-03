"""Live public-page enrichment pilot (Phase 2.2).

Selects a deterministic cohort of tracked Boshow events, fetches each event's public share page
live (rate-limited, timed), classifies + validates the response, extracts candidates, measures
incremental value vs the API/canonical-graph evidence, persists an auditable enrichment_run, and
computes an evidence-driven promotion recommendation. Measurement-only: it does not mutate
tracked_event or resolutions (the Phase 2.1 capture path owns those).
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from crawl_service.config import Settings, settings
from crawl_service.enrichment.clients import GraphReader
from crawl_service.enrichment.extractors import candidates_from_boshow_share
from crawl_service.enrichment.graph_evidence import candidates_from_graph
from crawl_service.enrichment.pilot import analysis, retrieval
from crawl_service.enrichment.pilot.decision import DecisionThresholds, recommend
from crawl_service.enrichment.registry import (
    CANONICAL_ENTITY_RELATIONSHIP,
    SOURCE_API,
)
from crawl_service.enrichment.service import EnrichmentService
from crawl_service.models import EnrichmentRun, EventFieldResolution, TrackedEvent

_SURFACE = "public_page"
_TRACKABLE = ("ACTIVE", "POST_EVENT")


def _uuid() -> str:
    return uuid.uuid4().hex


def _iso(dt):
    return dt.isoformat() if dt else None


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@dataclass
class FetchResult:
    status_code: int | None
    content_type: str | None
    body: str
    latency_ms: int
    bytes_received: int


class PilotService:
    def __init__(self, session_factory, graph_reader: GraphReader, enricher: EnrichmentService,
                 config: Settings | None = None, page_fetch=None) -> None:
        self._sf = session_factory
        self._graph = graph_reader
        self._enricher = enricher
        self._cfg = config or settings
        self._fetch = page_fetch or self._live_fetch

    async def _live_fetch(self, url: str) -> FetchResult:
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._cfg.capture_enrichment_public_page_timeout,
                                         follow_redirects=True) as client:
                r = await client.get(url, headers={"User-Agent": "n-quark/0.1 (+public enrichment)"})
                body = r.text
                return FetchResult(r.status_code, r.headers.get("content-type"), body,
                                   int((time.monotonic() - t0) * 1000), len(body.encode("utf-8")))
        except httpx.TimeoutException:
            return FetchResult(None, None, "", int((time.monotonic() - t0) * 1000), 0)
        except httpx.HTTPError:
            return FetchResult(-1, None, "", int((time.monotonic() - t0) * 1000), 0)

    def select_cohort(self, now: datetime) -> list[TrackedEvent]:
        min_age = timedelta(hours=self._cfg.capture_enrichment_pilot_min_tracked_age_hours)
        with self._sf() as s:
            rows = s.execute(
                select(TrackedEvent).where(
                    TrackedEvent.source.in_(tuple(self._cfg.capture_enrichment_source_set) or ("",)),
                    TrackedEvent.tracking_status.in_(_TRACKABLE),
                    TrackedEvent.canonical_event_id.is_not(None),
                )
            ).scalars().all()
        pool = [
            te for te in rows
            if (not self._cfg.capture_enrichment_pilot_priority_only or te.priority > 0)
            and (min_age.total_seconds() == 0 or (now - _aware(te.first_tracked_at)) >= min_age)
        ]
        pool.sort(key=lambda te: te.id)  # stable base order
        rng = random.Random(self._cfg.capture_enrichment_pilot_sample_seed)
        rng.shuffle(pool)
        k = min(len(pool), self._cfg.capture_enrichment_pilot_max_events,
                self._cfg.capture_enrichment_pilot_max_requests_per_run)
        return pool[:k]

    def _share_url(self, node_props: dict, source_record_id: str) -> str:
        return node_props.get("source_url") or f"{self._cfg.boshow_share_base}/{source_record_id}"

    def _existing_values(self, node, neighbors, event_id, now) -> tuple[dict, dict, dict]:
        graph_cands = candidates_from_graph(node, neighbors, observed_at=now)
        api_v = {c.field_name: c.normalized_value for c in graph_cands if c.source_type == SOURCE_API}
        graph_v = {c.field_name: c.normalized_value for c in graph_cands
                   if c.source_type == CANONICAL_ENTITY_RELATIONSHIP}
        current: dict[str, tuple[Any, datetime | None]] = {}
        with self._sf() as s:
            for r in s.execute(select(EventFieldResolution).where(
                EventFieldResolution.canonical_event_id == event_id,
                EventFieldResolution.is_current.is_(True))).scalars().all():
                current[r.field_name] = (r.resolved_value, _aware(r.resolved_at))
        return api_v, graph_v, current

    async def run(self, *, now: datetime | None = None, trace: bool = False) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        started = now
        cohort = self.select_cohort(now)
        m = _new_metrics()
        traces: list[dict] = []
        latencies: list[int] = []

        for i, te in enumerate(cohort):
            if i >= self._cfg.capture_enrichment_pilot_max_requests_per_run:
                break
            if i > 0 and self._fetch is self._live_fetch:
                await asyncio.sleep(self._cfg.capture_enrichment_public_page_rate_limit_ms / 1000.0)
            ev_trace = await self._process_event(te, now, m, latencies)
            if trace:
                traces.append(ev_trace)

        m["mean_request_latency_ms"] = round(sum(latencies) / len(latencies), 1) if latencies else 0
        m["mean_candidates_per_event"] = (
            round(m["candidates_created"] / m["valid_event_pages"], 2) if m["valid_event_pages"] else 0
        )
        thresholds = DecisionThresholds(
            min_retrieval_success=self._cfg.pilot_min_retrieval_success,
            min_incremental_gain_rate=self._cfg.pilot_min_incremental_gain_rate,
            max_conflict_rate=self._cfg.pilot_max_conflict_rate,
            min_parser_success=self._cfg.pilot_min_parser_success,
        )
        decision = recommend(m, thresholds)

        run_id = self._persist_run(now, started, cohort, m)
        result = {
            "run_id": run_id, "surface": _SURFACE, "events_selected": len(cohort),
            "metrics": m, "recommendation": decision,
        }
        if trace:
            result["events"] = traces
        return result

    async def _process_event(self, te: TrackedEvent, now, m, latencies) -> dict:
        node, neighbors = await self._graph.get_event(te.canonical_event_id)
        props = (node or {}).get("properties", {}) if node else {}
        url = self._share_url(props, te.source_record_id)
        m["pages_attempted"] += 1

        fetched = await self._fetch(url)
        latencies.append(fetched.latency_ms)
        m["bytes_received"] += fetched.bytes_received
        result_class = retrieval.classify_response(fetched.status_code, fetched.content_type, fetched.body)
        m["retrieval_outcomes"][result_class] = m["retrieval_outcomes"].get(result_class, 0) + 1

        ev: dict[str, Any] = {"source_record_id": te.source_record_id, "url_kind": "share",
                              "result_class": result_class, "steps": ["event_selected", "public_page_requested", "response_classified"]}
        if result_class in (retrieval.BLOCKED_OR_CHALLENGE,):
            m["challenge_or_block_count"] += 1
        if result_class != retrieval.SUCCESS_HTML:
            ev["suppressed"] = result_class
            return ev

        valid, reason = retrieval.validate_event_page(
            fetched.body, expected_title=props.get("display_name"), slug=te.source_record_id)
        ev["steps"].append("page_validated")
        ev["validation"] = reason
        if not valid:
            ev["suppressed"] = reason
            return ev

        m["pages_retrieved"] += 1
        m["valid_event_pages"] += 1
        body = fetched.body.lower()
        if "application/ld+json" in body:
            m["json_ld_present"] += 1
        if 'property="og:' in body:
            m["open_graph_present"] += 1
        if any(k in body for k in ("window.__", "__next_data__")):
            m["embedded_state_present"] += 1

        try:
            page_cands = candidates_from_boshow_share(fetched.body, source_url=url, observed_at=now)
        except Exception:  # noqa: BLE001 — parser robustness
            m["parser_failures"] += 1
            ev["suppressed"] = "PARSER_FAILED"
            return ev
        ev["steps"].append("candidates_extracted")

        if page_cands:
            self._enricher.store_candidates(te.canonical_event_id, te.source, te.source_record_id, page_cands, now)
            m["candidates_created"] += len(page_cands)

        api_v, graph_v, current = self._existing_values(node, neighbors, te.canonical_event_id, now)
        ev["steps"] += ["source_family_assigned", "compared_with_api_graph", "classified"]
        ev["fields"] = []
        for c in page_cands:
            cur = current.get(c.field_name)
            res = analysis.classify_candidate(
                c.field_name, c,
                api_value=api_v.get(c.field_name), graph_value=graph_v.get(c.field_name),
                current_value=cur[0] if cur else None,
                current_observed_at=cur[1] if cur else None,
                min_confidence=self._cfg.capture_enrichment_min_confidence,
            )
            m["fields_evaluated"] += 1
            cls = res["classification"]
            m["field_classifications"][cls] = m["field_classifications"].get(cls, 0) + 1
            fb = m["field_breakdown"].setdefault(c.field_name, {})
            fb[cls] = fb.get(cls, 0) + 1
            if cls == analysis.INCREMENTAL:
                m["incremental_field_gain_count"] += 1
            elif cls == analysis.DUPLICATE:
                m["duplicate_evidence_count"] += 1
            elif cls == analysis.CONFLICT:
                m["conflict_count"] += 1
            elif cls == analysis.FRESHNESS_GAIN:
                m["freshness_gain_count"] += 1
            ev["fields"].append({
                "field": c.field_name, "value": c.normalized_value, "surface": c.surface,
                "source_family": c.source_family, "independence_group": c.independence_group,
                "classification": cls, "reason": res["reason"],
            })
        return ev

    def _persist_run(self, now, started, cohort, m) -> str:
        run_id = _uuid()
        with self._sf() as s, s.begin():
            s.add(EnrichmentRun(
                id=run_id, source="boshow", surface=_SURFACE, status="COMPLETED",
                started_at=started, completed_at=now, events_selected=len(cohort),
                pages_attempted=m["pages_attempted"], pages_retrieved=m["pages_retrieved"],
                candidates_created=m["candidates_created"],
                resolutions_changed=m["incremental_field_gain_count"],
                conflicts_found=m["conflict_count"], parser_failures=m["parser_failures"],
                request_latency_ms=int(m["mean_request_latency_ms"]),
                bytes_received=m["bytes_received"], metrics=m,
                config_snapshot={
                    "max_events": self._cfg.capture_enrichment_pilot_max_events,
                    "seed": self._cfg.capture_enrichment_pilot_sample_seed,
                    "min_confidence": self._cfg.capture_enrichment_min_confidence,
                },
                created_at=now))
        return run_id


def _new_metrics() -> dict:
    return {
        "pages_attempted": 0, "pages_retrieved": 0, "valid_event_pages": 0,
        "challenge_or_block_count": 0, "parser_failures": 0,
        "json_ld_present": 0, "embedded_state_present": 0, "open_graph_present": 0,
        "candidates_created": 0, "fields_evaluated": 0,
        "incremental_field_gain_count": 0, "duplicate_evidence_count": 0,
        "conflict_count": 0, "freshness_gain_count": 0,
        "bytes_received": 0, "mean_request_latency_ms": 0, "mean_candidates_per_event": 0,
        "retrieval_outcomes": {}, "field_classifications": {}, "field_breakdown": {},
    }
