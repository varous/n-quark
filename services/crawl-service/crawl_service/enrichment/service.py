"""EnrichmentService (Phase 2.1) — gather field candidates from evidence surfaces, persist them with
provenance, resolve each supported field deterministically (versioned; history preserved), and report
an auditable outcome. Best-effort: it never raises into the capture path.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from crawl_service.config import Settings, settings
from crawl_service.enrichment import extractors
from crawl_service.enrichment.clients import GraphReader, PageFetcher
from crawl_service.enrichment.graph_evidence import candidates_from_graph
from crawl_service.enrichment.onsale import onsale_candidates
from crawl_service.enrichment.registry import (
    RESOLVER_VERSION,
    SCHEDULER_FIELDS,
    Candidate,
    valid_candidate,
)
from crawl_service.enrichment.resolver import (
    AUTO_RESOLVED,
    CONFLICT,
    REVIEW_CONFLICT,
    UNRESOLVED,
    Resolution,
    resolve_field,
)
from crawl_service.models import EnrichmentCandidate, EventFieldResolution

# outcome codes
ENRICHMENT_SUCCEEDED = "ENRICHMENT_SUCCEEDED"
ENRICHMENT_PARTIAL = "ENRICHMENT_PARTIAL"
ENRICHMENT_NO_EVIDENCE = "ENRICHMENT_NO_EVIDENCE"
ENRICHMENT_CONFLICT = "ENRICHMENT_CONFLICT"
ENRICHMENT_FAILED = "ENRICHMENT_FAILED"


def _uuid() -> str:
    return uuid.uuid4().hex


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class EnrichmentService:
    def __init__(self, session_factory, graph_reader: GraphReader, page_fetcher: PageFetcher | None,
                 config: Settings | None = None) -> None:
        self._sf = session_factory
        self._graph = graph_reader
        self._pages = page_fetcher
        self._cfg = config or settings

    async def enrich(
        self, *, canonical_event_id: str, source: str, source_record_id: str | None,
        source_url: str | None = None, currently_on_sale: bool = True,
        now: datetime | None = None, trace: bool = False,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        steps = ["event_captured"]
        suppressed: list[dict[str, str]] = []
        candidates: list[Candidate] = []
        graph_ok = page_ok = False

        # 1) canonical graph relationship evidence (highest confidence)
        try:
            node, neighbors = await self._graph.get_event(canonical_event_id)
            graph_ok = node is not None
            props = (node or {}).get("properties", {}) if node else {}
            title = props.get("display_name")
            source_url = source_url or props.get("source_url")  # public page for structured metadata
            candidates += candidates_from_graph(node, neighbors, observed_at=now)
            steps.append("canonical_relationship_resolved")
        except Exception:  # noqa: BLE001 — best-effort evidence
            suppressed.append({"source": "graph", "reason": "PARSER_FAILED"})
            title = None

        # 2) public-page structured metadata (flag-gated)
        if self._cfg.capture_enrichment_public_page_enabled and self._pages and source_url:
            steps.append("public_page_requested")
            fetched = await self._pages.fetch(source_url)
            if fetched is None:
                suppressed.append({"source": "public_page", "reason": "PARSER_FAILED"})
            else:
                page_ok = True
                html, text = fetched
                steps.append("structured_blocks_discovered")
                candidates += extractors.candidates_from_jsonld(
                    html, title=title, source_record_id=source_record_id, source_url=source_url, observed_at=now)
                candidates += extractors.candidates_from_embedded_state(html, source_url=source_url, observed_at=now)
                candidates += extractors.candidates_from_opengraph(html, source_url=source_url, observed_at=now)
                candidates += extractors.candidates_from_visible_text(text, source_url=source_url, observed_at=now)

        # 3) temporal on-sale evidence
        prev_first, prev_not = self._prev_onsale(canonical_event_id)
        candidates += onsale_candidates(
            observed_at=now, currently_on_sale=currently_on_sale,
            prev_first_ticket_state_seen_at=prev_first, prev_last_not_on_sale_at=prev_not)
        steps.append("candidates_extracted")

        valid = [c for c in candidates if valid_candidate(c)]
        for c in candidates:
            if not valid_candidate(c):
                suppressed.append({"field": c.field_name, "reason": "FIELD_NOT_PRESENT"})

        # 4) persist candidates (idempotent by content hash; supersede prior same-source values)
        active_by_field = self._persist_candidates(
            canonical_event_id, source, source_record_id, valid, now)
        steps.append("candidates_persisted")

        # 5) resolve each field with candidates
        resolutions: dict[str, Resolution] = {}
        for field_name in sorted(active_by_field):
            res = resolve_field(field_name, active_by_field[field_name],
                                min_confidence=self._cfg.capture_enrichment_min_confidence)
            resolutions[field_name] = res
            if res.reason_code == CONFLICT:
                suppressed.append({"field": field_name, "reason": "CONFLICTING_HIGH_AUTHORITY_VALUES"})
        self._persist_resolutions(canonical_event_id, resolutions, now)
        steps.append("fields_resolved")

        outcome = self._classify(valid, resolutions, graph_ok=graph_ok, page_ok=page_ok,
                                 page_attempted=bool(self._cfg.capture_enrichment_public_page_enabled and source_url))

        # scheduler-relevant, auto-resolved fields only
        resolved_for_scheduler = {
            f: r.resolved_value for f, r in resolutions.items()
            if f in SCHEDULER_FIELDS and r.review_status == AUTO_RESOLVED and r.resolved_value is not None
        }
        result = {
            "outcome": outcome,
            "resolved": resolved_for_scheduler,
            "fields": {f: self._res_summary(r) for f, r in resolutions.items()},
            "suppressed": suppressed,
        }
        if trace:
            result["trace"] = {"resolver_version": RESOLVER_VERSION, "steps": steps}
        return result

    def store_candidates(self, event_id, source, source_record_id, candidates, now=None) -> dict:
        """Public persist for externally-produced candidates (e.g. the live pilot)."""
        now = now or datetime.now(UTC)
        valid = [c for c in candidates if valid_candidate(c)]
        return self._persist_candidates(event_id, source, source_record_id, valid, now)

    # ---- persistence helpers --------------------------------------------------------------------
    def _prev_onsale(self, event_id: str) -> tuple[datetime | None, datetime | None]:
        with self._sf() as s:
            first = s.execute(
                select(EventFieldResolution.resolved_value).where(
                    EventFieldResolution.canonical_event_id == event_id,
                    EventFieldResolution.field_name == "first_ticket_state_seen_at",
                    EventFieldResolution.is_current.is_(True))
            ).scalar_one_or_none()
            noton = s.execute(
                select(EventFieldResolution.resolved_value).where(
                    EventFieldResolution.canonical_event_id == event_id,
                    EventFieldResolution.field_name == "last_observed_not_on_sale_at",
                    EventFieldResolution.is_current.is_(True))
            ).scalar_one_or_none()

        def _parse(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(str(v))
            except ValueError:
                return None

        return _parse(first), _parse(noton)

    def _persist_candidates(self, event_id, source, source_record_id, cands, now) -> dict[str, list[Candidate]]:
        active_by_field: dict[str, list[Candidate]] = {}
        with self._sf() as s, s.begin():
            for cand in cands:
                h = cand.content_hash
                existing = s.execute(
                    select(EnrichmentCandidate).where(
                        EnrichmentCandidate.canonical_event_id == event_id,
                        EnrichmentCandidate.field_name == cand.field_name,
                        EnrichmentCandidate.content_hash == h)
                ).scalar_one_or_none()
                if existing is None:
                    # supersede prior ACTIVE candidates from the same source type with a different value
                    s.execute(update(EnrichmentCandidate).where(
                        EnrichmentCandidate.canonical_event_id == event_id,
                        EnrichmentCandidate.field_name == cand.field_name,
                        EnrichmentCandidate.source_type == cand.source_type,
                        EnrichmentCandidate.content_hash != h,
                        EnrichmentCandidate.candidate_status == "ACTIVE",
                    ).values(candidate_status="SUPERSEDED"))
                    s.add(EnrichmentCandidate(
                        id=_uuid(), canonical_event_id=event_id, source=source,
                        source_record_id=source_record_id, field_name=cand.field_name,
                        candidate_value=cand.candidate_value, normalized_value=cand.normalized_value,
                        source_type=cand.source_type, surface=cand.surface,
                        source_family=cand.source_family, independence_group=cand.independence_group,
                        source_url=cand.source_url, extraction_method=cand.extraction_method,
                        epistemic_status=cand.epistemic_status, observed_at=cand.observed_at,
                        source_published_at=cand.source_published_at, confidence=cand.confidence,
                        content_hash=h, candidate_status="ACTIVE", created_at=now))
            s.flush()  # SessionLocal is autoflush=False; make new rows visible to the reload below
            # reload ACTIVE candidates for the event's touched fields
            fields = {c.field_name for c in cands}
            for f in fields:
                rows = s.execute(
                    select(EnrichmentCandidate).where(
                        EnrichmentCandidate.canonical_event_id == event_id,
                        EnrichmentCandidate.field_name == f,
                        EnrichmentCandidate.candidate_status == "ACTIVE")
                ).scalars().all()
                active_by_field[f] = [self._row_to_candidate(r) for r in rows]
        return active_by_field

    def _row_to_candidate(self, r: EnrichmentCandidate) -> Candidate:
        c = Candidate(
            field_name=r.field_name, candidate_value=r.candidate_value, source_type=r.source_type,
            extraction_method=r.extraction_method, confidence=r.confidence,
            observed_at=_aware(r.observed_at), source_url=r.source_url,
            epistemic_status=r.epistemic_status)
        c.normalized_value = r.normalized_value
        return c

    def _persist_resolutions(self, event_id, resolutions: dict[str, Resolution], now) -> None:
        with self._sf() as s, s.begin():
            for field_name, res in resolutions.items():
                current = s.execute(
                    select(EventFieldResolution).where(
                        EventFieldResolution.canonical_event_id == event_id,
                        EventFieldResolution.field_name == field_name,
                        EventFieldResolution.is_current.is_(True))
                ).scalar_one_or_none()
                supporting = [self._cand_id(s, event_id, c) for c in res.supporting]
                contradicting = [self._cand_id(s, event_id, c) for c in res.contradicting]
                if current is None:
                    s.add(self._new_resolution(event_id, res, 1, now, supporting, contradicting))
                    continue
                changed = (str(current.resolved_value) != str(res.resolved_value)
                           or current.resolution_method != res.resolution_method
                           or current.review_status != res.review_status)
                if changed:
                    current.is_current = False
                    current.updated_at = now
                    s.add(self._new_resolution(event_id, res, current.version + 1, now,
                                               supporting, contradicting))
                else:
                    current.confidence = res.confidence
                    current.supporting_candidate_ids = supporting
                    current.contradicting_candidate_ids = contradicting
                    current.updated_at = now

    def _cand_id(self, session, event_id, cand: Candidate) -> str | None:
        row = session.execute(
            select(EnrichmentCandidate.id).where(
                EnrichmentCandidate.canonical_event_id == event_id,
                EnrichmentCandidate.field_name == cand.field_name,
                EnrichmentCandidate.content_hash == cand.content_hash)
        ).scalar_one_or_none()
        return row

    def _new_resolution(self, event_id, res: Resolution, version, now, supporting, contradicting):
        return EventFieldResolution(
            id=_uuid(), canonical_event_id=event_id, field_name=res.field_name,
            resolved_value=res.resolved_value, resolution_method=res.resolution_method,
            confidence=res.confidence, review_status=res.review_status, reason_code=res.reason_code,
            supporting_candidate_ids=[x for x in supporting if x],
            contradicting_candidate_ids=[x for x in contradicting if x],
            resolver_version=RESOLVER_VERSION, version=version, is_current=True,
            resolved_at=now, created_at=now, updated_at=now)

    @staticmethod
    def _res_summary(r: Resolution) -> dict[str, Any]:
        return {"value": r.resolved_value, "method": r.resolution_method, "confidence": r.confidence,
                "review_status": r.review_status, "reason": r.reason_code,
                "supporting": len(r.supporting), "contradicting": len(r.contradicting)}

    @staticmethod
    def _classify(valid, resolutions, *, graph_ok, page_ok, page_attempted) -> str:
        if not valid:
            if not graph_ok and page_attempted and not page_ok:
                return ENRICHMENT_FAILED
            return ENRICHMENT_NO_EVIDENCE
        reviews = {r.review_status for r in resolutions.values()}
        if REVIEW_CONFLICT in reviews:
            return ENRICHMENT_CONFLICT
        auto = any(r.review_status == AUTO_RESOLVED for r in resolutions.values())
        unresolved = any(r.resolution_method == UNRESOLVED or r.review_status != AUTO_RESOLVED
                         for r in resolutions.values())
        if auto and not unresolved:
            return ENRICHMENT_SUCCEEDED
        if auto:
            return ENRICHMENT_PARTIAL
        return ENRICHMENT_NO_EVIDENCE

    # ---- read (for the internal endpoint) -------------------------------------------------------
    def get_enrichment(self, event_id: str) -> dict[str, Any]:
        with self._sf() as s:
            resolutions = s.execute(
                select(EventFieldResolution).where(
                    EventFieldResolution.canonical_event_id == event_id,
                    EventFieldResolution.is_current.is_(True))
            ).scalars().all()
            candidates = s.execute(
                select(EnrichmentCandidate).where(
                    EnrichmentCandidate.canonical_event_id == event_id)
                .order_by(EnrichmentCandidate.field_name, EnrichmentCandidate.created_at.desc())
            ).scalars().all()
            return {
                "canonical_event_id": event_id,
                "resolved_fields": {
                    r.field_name: {
                        "value": r.resolved_value, "method": r.resolution_method,
                        "confidence": r.confidence, "review_status": r.review_status,
                        "reason": r.reason_code, "version": r.version,
                        "supporting_candidate_ids": r.supporting_candidate_ids,
                        "contradicting_candidate_ids": r.contradicting_candidate_ids,
                        "resolved_at": _iso(_aware(r.resolved_at)),
                    } for r in resolutions},
                "candidates": [
                    {"id": c.id, "field": c.field_name, "value": c.normalized_value,
                     "source_type": c.source_type, "extraction_method": c.extraction_method,
                     "confidence": c.confidence, "status": c.candidate_status,
                     "source_url": c.source_url, "observed_at": _iso(_aware(c.observed_at))}
                    for c in candidates],
            }
