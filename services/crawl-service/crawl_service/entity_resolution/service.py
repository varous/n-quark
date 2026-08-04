"""EntityResolutionService (Phase 3.1).

Turns captured source events into a shared canonical entity graph: extracts artist/venue/organizer/
series evidence, resolves each deterministically against the entities already known (handle registry +
prior canonicals), persists the decision (audit + queue + history), and writes canonical nodes and
relationships into the graph — source-handle -> IDENTIFIES -> canonical, and event -> FEATURES /
OCCURS_AT / ORGANIZED_BY / PART_OF_SERIES. Best-effort: it never raises into the capture path, and
platform-exclusive events stay distinct (shared entities never imply a duplicate event)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from crawl_service.config import Settings, settings
from crawl_service.entity_resolution import resolvers as R
from crawl_service.entity_resolution.evidence import (
    ARTIST,
    EVENT_SERIES,
    ORGANIZER,
    VENUE,
    EntityEvidence,
    EventEntities,
    extract_event_entities,
)
from crawl_service.entity_resolution.resolvers import KnownEntities, ResolutionResult
from crawl_service.models import (
    EntityResolutionCandidate,
    EntityResolutionHistory,
    EntitySourceHandle,
    TrackedEvent,
)

# per-event outcome codes
ER_SUCCEEDED = "ENTITY_RESOLUTION_SUCCEEDED"
ER_PARTIAL = "ENTITY_RESOLUTION_PARTIAL"
ER_AMBIGUOUS = "ENTITY_RESOLUTION_AMBIGUOUS"
ER_NO_EVIDENCE = "ENTITY_RESOLUTION_NO_EVIDENCE"
ER_FAILED = "ENTITY_RESOLUTION_FAILED"

_REL = {ARTIST: "FEATURES", VENUE: "OCCURS_AT", ORGANIZER: "ORGANIZED_BY", EVENT_SERIES: "PART_OF_SERIES"}
_NODE_TYPE = {ARTIST: "artist", VENUE: "venue", ORGANIZER: "organizer", EVENT_SERIES: "event_series"}


def _uuid() -> str:
    return uuid.uuid4().hex


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class EntityResolutionService:
    def __init__(self, session_factory, graph_reader, graph_writer, config: Settings | None = None) -> None:
        self._sf = session_factory
        self._graph = graph_reader
        self._writer = graph_writer
        self._cfg = config or settings

    # ---- single event ---------------------------------------------------------------------------
    async def resolve_event(self, *, canonical_event_id: str, source: str, source_record_id: str,
                            now: datetime | None = None, trace: bool = False) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        node, neighbors = await self._graph.get_event(canonical_event_id)
        ents = extract_event_entities(
            canonical_event_id=canonical_event_id, source=source, source_record_id=source_record_id,
            node=node, neighbors=neighbors, observed_at=now)
        evidence_list = ents.all()
        if not evidence_list:
            return {"outcome": ER_NO_EVIDENCE, "canonical_event_id": canonical_event_id,
                    "entities": [], "geography": self._geography(ents)}

        known = self._known_for(evidence_list)
        decisions: list[tuple[EntityEvidence, ResolutionResult]] = []
        for ev in evidence_list:
            res = R.resolve(ev, known)
            res = self._apply_threshold(ev.entity_type, res)
            self._persist_candidate(ev, res, now)
            if res.status == R.RESOLVED and res.canonical_entity_id:
                self._register_handle(ev, res, now)
                # let subsequent same-run evidence converge onto the just-created canonical
                known.handle_map[(ev.source, ev.source_entity_handle)] = res.canonical_entity_id
                known.name_map.setdefault(ev.normalized_name, set()).add(res.canonical_entity_id)
                if ev.entity_type == VENUE:
                    known.venue_map.setdefault(ev.normalized_name, []).append(
                        (res.canonical_entity_id, ev.evidence.get("city")))
            decisions.append((ev, res))

        await self._write_graph(canonical_event_id, decisions)
        outcome = self._classify(decisions)
        result = {"outcome": outcome, "canonical_event_id": canonical_event_id,
                  "entities": [self._decision_summary(ev, res) for ev, res in decisions],
                  "geography": self._geography(ents)}
        if trace:
            result["trace"] = {"resolver_version": R.RESOLVER_VERSION,
                               "steps": ["event_read", "evidence_extracted", "known_entities_loaded",
                                         "entities_resolved", "candidates_persisted",
                                         "handles_registered", "graph_relationships_written",
                                         "geography_derived", "outcome_classified"]}
        return result

    # ---- batch ----------------------------------------------------------------------------------
    async def run(self, *, sources: list[str] | None = None, limit: int | None = None,
                  now: datetime | None = None, trace: bool = False) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        limit = limit or self._cfg.entity_resolution_max_events_per_run
        source_set = set(sources) if sources else set(self._cfg.entity_resolution_source_set)
        with self._sf() as s:
            rows = s.execute(
                select(TrackedEvent.source, TrackedEvent.source_record_id, TrackedEvent.canonical_event_id)
                .where(TrackedEvent.source.in_(tuple(source_set) or ("",)),
                       TrackedEvent.canonical_event_id.is_not(None))
                .limit(limit)
            ).all()
        counts = {ER_SUCCEEDED: 0, ER_PARTIAL: 0, ER_AMBIGUOUS: 0, ER_NO_EVIDENCE: 0, ER_FAILED: 0}
        traces: list[dict] = []
        for src, sid, cid in rows:
            try:
                res = await self.resolve_event(canonical_event_id=cid, source=src,
                                               source_record_id=sid, now=now, trace=trace)
            except Exception as exc:  # noqa: BLE001 — one bad event never aborts the batch
                counts[ER_FAILED] += 1
                if trace:
                    traces.append({"canonical_event_id": cid, "source": src, "error": str(exc)})
                continue
            counts[res["outcome"]] = counts.get(res["outcome"], 0) + 1
            if trace:
                traces.append({"canonical_event_id": cid, "source": src, "outcome": res["outcome"],
                               "entities": res["entities"]})
        summary = {"events_processed": len(rows), "outcomes": counts, "sources": sorted(source_set)}
        if trace:
            summary["events"] = traces
        return summary

    # ---- known-entity loading -------------------------------------------------------------------
    def _known_for(self, evidence_list: list[EntityEvidence]) -> KnownEntities:
        handles = {(e.source, e.source_entity_handle) for e in evidence_list}
        names_by_type: dict[str, set[str]] = {}
        for e in evidence_list:
            names_by_type.setdefault(e.entity_type, set()).add(e.normalized_name)
        known = KnownEntities()
        with self._sf() as s:
            for src, h in handles:
                cid = s.execute(
                    select(EntitySourceHandle.canonical_entity_id).where(
                        EntitySourceHandle.source == src, EntitySourceHandle.source_entity_handle == h)
                ).scalar_one_or_none()
                if cid:
                    known.handle_map[(src, h)] = cid
            for etype, names in names_by_type.items():
                rows = s.execute(
                    select(EntityResolutionCandidate.normalized_name,
                           EntityResolutionCandidate.candidate_canonical_entity_id,
                           EntityResolutionCandidate.evidence)
                    .where(EntityResolutionCandidate.entity_type == etype,
                           EntityResolutionCandidate.normalized_name.in_(tuple(names) or ("",)),
                           EntityResolutionCandidate.resolution_status == R.RESOLVED,
                           EntityResolutionCandidate.candidate_canonical_entity_id.is_not(None))
                ).all()
                for nm, cid, evd in rows:
                    known.name_map.setdefault(nm, set()).add(cid)
                    if etype == VENUE:
                        known.venue_map.setdefault(nm, []).append((cid, (evd or {}).get("city")))
        return known

    def _apply_threshold(self, entity_type: str, res: ResolutionResult) -> ResolutionResult:
        """A RESOLVED decision below the entity's configured confidence is downgraded to POSSIBLE_MATCH,
        so the auto-resolve thresholds are the real gate for confident convergence."""
        if res.status == R.RESOLVED and res.score < self._cfg.entity_auto_threshold(entity_type):
            res.status = R.POSSIBLE_MATCH
            res.contradicting = [*res.contradicting, "BELOW_AUTO_THRESHOLD"]
        return res

    # ---- persistence ----------------------------------------------------------------------------
    def _persist_candidate(self, ev: EntityEvidence, res: ResolutionResult, now: datetime) -> None:
        with self._sf() as s, s.begin():
            existing = s.execute(
                select(EntityResolutionCandidate).where(
                    EntityResolutionCandidate.entity_type == ev.entity_type,
                    EntityResolutionCandidate.source == ev.source,
                    EntityResolutionCandidate.source_record_id == ev.source_record_id,
                    EntityResolutionCandidate.source_entity_handle == ev.source_entity_handle)
            ).scalar_one_or_none()
            if existing is None:
                cand_id = _uuid()
                s.add(EntityResolutionCandidate(
                    id=cand_id, entity_type=ev.entity_type, source=ev.source,
                    source_record_id=ev.source_record_id, canonical_event_id=ev.canonical_event_id,
                    source_entity_handle=ev.source_entity_handle, raw_name=ev.raw_name,
                    normalized_name=ev.normalized_name,
                    candidate_canonical_entity_id=res.canonical_entity_id, match_score=res.score,
                    resolution_status=res.status, reason_code=res.reason_code,
                    supporting_signals=res.supporting, contradicting_signals=res.contradicting,
                    evidence=ev.evidence, resolver_version=res.resolver_version,
                    observed_at=ev.observed_at, created_at=now, updated_at=now))
                s.add(EntityResolutionHistory(
                    id=_uuid(), candidate_id=cand_id, previous_status=None, new_status=res.status,
                    previous_canonical_entity_id=None, new_canonical_entity_id=res.canonical_entity_id,
                    reason_code=res.reason_code, resolver_version=res.resolver_version, created_at=now))
                return
            changed = (existing.resolution_status != res.status
                       or existing.candidate_canonical_entity_id != res.canonical_entity_id)
            if changed:
                s.add(EntityResolutionHistory(
                    id=_uuid(), candidate_id=existing.id,
                    previous_status=existing.resolution_status, new_status=res.status,
                    previous_canonical_entity_id=existing.candidate_canonical_entity_id,
                    new_canonical_entity_id=res.canonical_entity_id, reason_code=res.reason_code,
                    resolver_version=res.resolver_version, created_at=now))
            existing.resolution_status = res.status
            existing.candidate_canonical_entity_id = res.canonical_entity_id
            existing.match_score = res.score
            existing.reason_code = res.reason_code
            existing.supporting_signals = res.supporting
            existing.contradicting_signals = res.contradicting
            existing.evidence = ev.evidence
            existing.resolver_version = res.resolver_version
            existing.updated_at = now

    def _register_handle(self, ev: EntityEvidence, res: ResolutionResult, now: datetime) -> None:
        with self._sf() as s, s.begin():
            existing = s.execute(
                select(EntitySourceHandle).where(
                    EntitySourceHandle.source == ev.source,
                    EntitySourceHandle.source_entity_handle == ev.source_entity_handle)
            ).scalar_one_or_none()
            if existing is None:
                s.add(EntitySourceHandle(
                    id=_uuid(), entity_type=ev.entity_type, source=ev.source,
                    source_entity_handle=ev.source_entity_handle,
                    source_url=(ev.provenance or {}).get("source_url"),
                    canonical_entity_id=res.canonical_entity_id, confidence=res.score,
                    resolution_method=res.reason_code, first_seen=now, last_seen=now))
            else:
                existing.canonical_entity_id = res.canonical_entity_id
                existing.confidence = max(existing.confidence, res.score)
                existing.last_seen = now

    # ---- graph writes ---------------------------------------------------------------------------
    async def _write_graph(self, event_id: str, decisions) -> None:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        for ev, res in decisions:
            if res.status not in (R.RESOLVED, R.POSSIBLE_MATCH) or not res.canonical_entity_id:
                continue
            cid = res.canonical_entity_id
            props: dict[str, Any] = {"display_name": ev.raw_name}
            if ev.entity_type == VENUE:
                if ev.evidence.get("city"):
                    props["city"] = ev.evidence["city"]
                if ev.evidence.get("region_id"):
                    props["region_id"] = ev.evidence["region_id"]
            nodes.append({"id": cid, "type": _NODE_TYPE[ev.entity_type], "properties": props})
            # source handle -> IDENTIFIES -> canonical entity
            nodes.append({"id": ev.source_entity_handle, "type": "source_handle",
                          "properties": {"source": ev.source, "raw_name": ev.raw_name}})
            edges.append({"source": ev.source_entity_handle, "relationship": "IDENTIFIES",
                          "target": cid, "properties": {"confidence": res.score}})
            # event -> REL -> canonical entity (tagged with status so POSSIBLE stays distinguishable)
            edges.append({"source": event_id, "relationship": _REL[ev.entity_type], "target": cid,
                          "properties": {"resolution_status": res.status, "confidence": res.score}})
            # venue geography: venue -> IN_REGION -> region (when the source event carried a region)
            if ev.entity_type == VENUE and ev.evidence.get("region_id"):
                edges.append({"source": cid, "relationship": "IN_REGION",
                              "target": ev.evidence["region_id"], "properties": {}})
        if nodes or edges:
            await self._writer.upsert_batch(nodes, edges)

    # ---- geography failure modes ----------------------------------------------------------------
    @staticmethod
    def _geography(ents: EventEntities) -> dict[str, Any]:
        if ents.venue is None:
            return {"status": "NO_VENUE_TEXT", "city": ents.city, "region_id": ents.region_id}
        city = ents.venue.evidence.get("city")
        region = ents.venue.evidence.get("region_id")
        if ents.venue.evidence.get("is_generic") and not city:
            status = "AMBIGUOUS_VENUE"
        elif not city and not region:
            status = "VENUE_HAS_NO_GEOGRAPHY"
        else:
            status = "DIRECT_SOURCE_GEOGRAPHY_ONLY"
        return {"status": status, "city": city, "region_id": region}

    # ---- classification + summaries -------------------------------------------------------------
    @staticmethod
    def _classify(decisions) -> str:
        statuses = [res.status for _, res in decisions]
        resolved = sum(1 for st in statuses if st == R.RESOLVED)
        ambiguous = sum(1 for st in statuses if st == R.AMBIGUOUS)
        other = len(statuses) - resolved
        if resolved and other == 0:
            return ER_SUCCEEDED
        if resolved:
            return ER_PARTIAL
        if ambiguous:
            return ER_AMBIGUOUS
        return ER_PARTIAL

    @staticmethod
    def _decision_summary(ev: EntityEvidence, res: ResolutionResult) -> dict[str, Any]:
        return {"entity_type": ev.entity_type, "source": ev.source, "raw_name": ev.raw_name,
                "normalized_name": ev.normalized_name, "handle": ev.source_entity_handle,
                "status": res.status, "canonical_entity_id": res.canonical_entity_id,
                "score": res.score, "reason": res.reason_code, "supporting": res.supporting,
                "contradicting": res.contradicting}

    # ---- reads / metrics ------------------------------------------------------------------------
    def coverage(self, *, source: str | None = None) -> dict[str, Any]:
        with self._sf() as s:
            rows = s.execute(select(EntityResolutionCandidate)).scalars().all()
            handles = s.execute(select(EntitySourceHandle)).scalars().all()
        if source:
            rows = [r for r in rows if r.source == source]
        # canonical -> set of sources (for cross-source convergence), from confident handles
        canon_sources: dict[str, set[str]] = {}
        for h in handles:
            canon_sources.setdefault(h.canonical_entity_id, set()).add(h.source)
        out: dict[str, Any] = {"source": source or "all", "by_entity_type": {}}
        for etype in (ARTIST, VENUE, ORGANIZER, EVENT_SERIES):
            er = [r for r in rows if r.entity_type == etype]
            resolved = [r for r in er if r.resolution_status == R.RESOLVED and r.candidate_canonical_entity_id]
            canon_ids = {r.candidate_canonical_entity_id for r in resolved}
            cross = {c for c in canon_ids if len(canon_sources.get(c, set())) > 1}
            out["by_entity_type"][etype] = {
                "mentions": len(er),
                "resolved_mentions": len(resolved),
                "possible_mentions": sum(1 for r in er if r.resolution_status == R.POSSIBLE_MATCH),
                "ambiguous_mentions": sum(1 for r in er if r.resolution_status == R.AMBIGUOUS),
                "unresolved_mentions": sum(1 for r in er if r.resolution_status == R.UNRESOLVED),
                "unique_canonical_entities": len(canon_ids),
                "cross_source_canonical_entities": len(cross),
                "resolution_rate": round(len(resolved) / len(er), 4) if er else 0.0,
            }
        return out

    def unresolved(self, *, entity_type: str | None = None, source: str | None = None,
                   limit: int = 100) -> dict[str, Any]:
        statuses = (R.UNRESOLVED, R.AMBIGUOUS, R.POSSIBLE_MATCH)
        with self._sf() as s:
            stmt = select(EntityResolutionCandidate).where(
                EntityResolutionCandidate.resolution_status.in_(statuses))
            if entity_type:
                stmt = stmt.where(EntityResolutionCandidate.entity_type == entity_type)
            if source:
                stmt = stmt.where(EntityResolutionCandidate.source == source)
            rows = s.execute(stmt.limit(limit)).scalars().all()
            items = [self._row_summary(r) for r in rows]
        return {"count": len(items), "items": items}

    def candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self._sf() as s:
            row = s.get(EntityResolutionCandidate, candidate_id)
            if row is None:
                return None
            history = s.execute(
                select(EntityResolutionHistory)
                .where(EntityResolutionHistory.candidate_id == candidate_id)
                .order_by(EntityResolutionHistory.created_at)
            ).scalars().all()
            data = self._row_summary(row)
            data["history"] = [{
                "previous_status": h.previous_status, "new_status": h.new_status,
                "previous_canonical": h.previous_canonical_entity_id,
                "new_canonical": h.new_canonical_entity_id, "reason": h.reason_code,
                "at": _iso(_aware(h.created_at))} for h in history]
            return data

    def source_handles(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        with self._sf() as s:
            rows = s.execute(
                select(EntitySourceHandle).where(
                    EntitySourceHandle.canonical_entity_id == entity_id)
            ).scalars().all()
        return {"canonical_entity_id": entity_id, "entity_type": entity_type,
                "handles": [{"source": h.source, "handle": h.source_entity_handle,
                             "confidence": h.confidence, "method": h.resolution_method,
                             "first_seen": _iso(_aware(h.first_seen)),
                             "last_seen": _iso(_aware(h.last_seen))} for h in rows]}

    def resolved_entities(self, event_id: str) -> dict[str, Any]:
        with self._sf() as s:
            rows = s.execute(
                select(EntityResolutionCandidate).where(
                    EntityResolutionCandidate.canonical_event_id == event_id)
            ).scalars().all()
        return {"canonical_event_id": event_id, "entities": [self._row_summary(r) for r in rows]}

    def cross_inventory(self, *, entity_type: str = ARTIST, limit: int = 50) -> dict[str, Any]:
        """Minimal proof that platform-exclusive events converge through shared entities: canonical
        entities carrying events from more than one source."""
        with self._sf() as s:
            handles = s.execute(
                select(EntitySourceHandle).where(EntitySourceHandle.entity_type == entity_type)
            ).scalars().all()
            cands = s.execute(
                select(EntityResolutionCandidate).where(
                    EntityResolutionCandidate.entity_type == entity_type,
                    EntityResolutionCandidate.resolution_status == R.RESOLVED)
            ).scalars().all()
        canon_sources: dict[str, set[str]] = {}
        for h in handles:
            canon_sources.setdefault(h.canonical_entity_id, set()).add(h.source)
        agg: dict[str, dict[str, Any]] = {}
        for c in cands:
            cid = c.candidate_canonical_entity_id
            if not cid:
                continue
            a = agg.setdefault(cid, {"canonical_entity_id": cid, "display_name": c.raw_name,
                                     "by_source": {}, "cities": set(), "events": set()})
            a["by_source"][c.source] = a["by_source"].get(c.source, 0) + 1
            if c.evidence.get("city"):
                a["cities"].add(c.evidence["city"])
            if c.canonical_event_id:
                a["events"].add(c.canonical_event_id)
        rows = []
        for cid, a in agg.items():
            if len(canon_sources.get(cid, set())) > 1:
                rows.append({"canonical_entity_id": cid, "display_name": a["display_name"],
                             "by_source": a["by_source"], "cities": sorted(a["cities"]),
                             "event_count": len(a["events"]),
                             "sources": sorted(canon_sources.get(cid, set()))})
        rows.sort(key=lambda r: (-len(r["sources"]), -r["event_count"]))
        return {"entity_type": entity_type, "count": len(rows), "cross_source_entities": rows[:limit]}

    @staticmethod
    def _row_summary(r: EntityResolutionCandidate) -> dict[str, Any]:
        return {"id": r.id, "entity_type": r.entity_type, "source": r.source,
                "source_record_id": r.source_record_id, "canonical_event_id": r.canonical_event_id,
                "handle": r.source_entity_handle, "raw_name": r.raw_name,
                "normalized_name": r.normalized_name,
                "canonical_entity_id": r.candidate_canonical_entity_id, "status": r.resolution_status,
                "score": r.match_score, "reason": r.reason_code, "supporting": r.supporting_signals,
                "contradicting": r.contradicting_signals, "evidence": r.evidence,
                "observed_at": _iso(_aware(r.observed_at))}
