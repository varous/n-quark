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
from crawl_service.entity_resolution import normalizers as _N
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
        cross_index = self._cross_type_index()
        decisions: list[tuple[EntityEvidence, ResolutionResult]] = []
        for ev in evidence_list:
            # 5B.2.4 — interpretation GATE: no unresolved interpretation state may create a canonical.
            gate = self._interpret_gate(ev, cross_index, known)
            if gate is not None:
                res = ResolutionResult(status=gate, canonical_entity_id=None, score=0.0,
                                       reason_code=gate)
                self._persist_candidate(ev, res, now)
                decisions.append((ev, res))
                continue
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

    # ---- interpretation gate (5B.2.4) -----------------------------------------------------------
    def _cross_type_index(self) -> dict[str, set[str]]:
        """slug(name) -> set of canonical entity types it exists as (RESOLVED only, non-quarantined)."""
        idx: dict[str, set[str]] = {}
        with self._sf() as s:
            rows = s.execute(
                select(EntityResolutionCandidate.entity_type, EntityResolutionCandidate.raw_name)
                .where(EntityResolutionCandidate.resolution_status == R.RESOLVED,
                       EntityResolutionCandidate.candidate_canonical_entity_id.is_not(None))).all()
        for etype, raw in rows:
            idx.setdefault(_N.slug(raw), set()).add(etype)
        return idx

    def _interpret_gate(self, ev: EntityEvidence, cross_index: dict[str, set[str]],
                        known: KnownEntities) -> str | None:
        """Return a non-product status (REVIEW_REQUIRED / ROLE_CONFLICT / PLACEHOLDER) when this mention
        must NOT create/match a canonical, else None. Consumes the mention's interpretation (set at
        extraction) plus a registry cross-type check — one place, no duplicated interpretation logic."""
        interp = (ev.evidence or {}).get("interpretation") or {}
        state = interp.get("state")
        if state == "PLACEHOLDER":
            return R.PLACEHOLDER
        if state == "AMBIGUOUS_COMPOUND":
            return R.REVIEW_REQUIRED
        # cross-type / role conflict — identity already exists as a DIFFERENT canonical type. Only gate a
        # NEW wrong-type creation: an ESTABLISHED same-type canonical (already in known.name_map or a
        # matching source handle) is a confirmed entity and resolves normally even if it is legitimately
        # dual-role. This protects real dual-role entities from being suppressed on re-resolution.
        if (ev.evidence or {}).get("multi_role_confirmed"):
            return None
        has_same_type_canonical = (ev.normalized_name in known.name_map
                                   or (ev.source, ev.source_entity_handle) in known.handle_map)
        if has_same_type_canonical:
            return None
        other = sorted(t for t in cross_index.get(_N.slug(ev.raw_name), set()) if t != ev.entity_type)
        if other:
            return R.ROLE_CONFLICT
        return None

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

    def interpreted_relationships(self, event_id: str) -> dict[str, Any]:
        """Integrity-projected Event relationships (5B.2.5). A quarantined/placeholder mention is NEVER
        presented as a legitimate linked entity — the venue reads NOT_ANNOUNCED (raw source preserved);
        review/role-conflict mentions read NEEDS_REVIEW; only RESOLVED canonicals are real links. The
        frontend consumes this rather than inferring integrity from graph edges or display strings."""
        with self._sf() as s:
            rows = s.execute(
                select(EntityResolutionCandidate).where(
                    EntityResolutionCandidate.canonical_event_id == event_id)).scalars().all()

        def _mention(r: EntityResolutionCandidate) -> dict[str, Any]:
            return {"candidate_id": r.id, "raw_name": r.raw_name, "source": r.source,
                    "status": r.resolution_status, "reason": r.reason_code,
                    "canonical_entity_id": r.candidate_canonical_entity_id}

        def _bucket(r: EntityResolutionCandidate) -> str:
            st = r.resolution_status
            if st in (R.RESOLVED, R.POSSIBLE_MATCH) and r.candidate_canonical_entity_id:
                return "RESOLVED"
            if st in (R.PLACEHOLDER, R.QUARANTINED):
                return "SUPPRESSED"      # absence marker / repaired bad canonical — not a real link
            if st in (R.REVIEW_REQUIRED, R.ROLE_CONFLICT, R.AMBIGUOUS):
                return "NEEDS_REVIEW"
            return "UNRESOLVED"

        artists = {"resolved": [], "needs_review": [], "unresolved_mentions": []}
        venue = {"state": "NOT_PROVIDED", "canonical_entity_id": None, "raw_mentions": []}
        organizer = {"state": "NOT_PROVIDED", "canonical_entity_id": None, "raw_mentions": []}

        def _single(slot: dict[str, Any], r: EntityResolutionCandidate, bucket: str) -> None:
            slot["raw_mentions"].append(r.raw_name)
            rank = {"RESOLVED": 3, "NEEDS_REVIEW": 2, "SUPPRESSED": 1, "UNRESOLVED": 0}
            cur = {"PRESENT": 3, "NEEDS_REVIEW": 2, "NOT_ANNOUNCED": 1, "NOT_PROVIDED": 0}.get(slot["state"], 0)
            new_state = {"RESOLVED": "PRESENT", "NEEDS_REVIEW": "NEEDS_REVIEW",
                         "SUPPRESSED": "NOT_ANNOUNCED", "UNRESOLVED": "NOT_PROVIDED"}[bucket]
            if rank[bucket] >= cur:
                slot["state"] = new_state
                slot["canonical_entity_id"] = r.candidate_canonical_entity_id if bucket == "RESOLVED" else None

        for r in rows:
            b = _bucket(r)
            if r.entity_type == ARTIST:
                if b == "RESOLVED":
                    artists["resolved"].append(_mention(r))
                elif b == "NEEDS_REVIEW":
                    artists["needs_review"].append(_mention(r))
                elif b == "UNRESOLVED":
                    artists["unresolved_mentions"].append(_mention(r))
                # SUPPRESSED artists (placeholder) are dropped from product (raw kept in the mention row)
            elif r.entity_type == VENUE:
                _single(venue, r, b)
            elif r.entity_type == ORGANIZER:
                _single(organizer, r, b)

        return {
            "canonical_event_id": event_id,
            "artists": {
                "resolved": artists["resolved"],
                "resolved_count": len(artists["resolved"]),
                "needs_review": artists["needs_review"],
                "needs_review_count": len(artists["needs_review"]),
                "unresolved_mentions": artists["unresolved_mentions"]},
            "venue": venue,
            "organizer": organizer,
            "note": "integrity-projected: quarantined/placeholder mentions are never exposed as canonical "
                    "links; raw source values are preserved for evidence.",
        }

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

    # ---- canonical-entity listing (admin read layer) --------------------------------------------
    def _identity_state(self, canonical_id: str, entity_type: str, statuses: set[str],
                        all_canon_ids: set[str], source_count: int) -> str:
        """Best-effort identity classification for the admin console (observe, don't migrate)."""
        # A city-scoped venue (`venue:name--city`) whose non-scoped sibling `venue:name` also exists
        # as a canonical is a possible legacy/naive-projection duplicate.
        if entity_type == "VENUE" and "--" in canonical_id:
            base = canonical_id.rsplit("--", 1)[0]
            if base in all_canon_ids:
                return "POSSIBLE_DUPLICATE"
        if "RESOLVED" not in statuses:
            return "UNRESOLVED"
        if source_count > 1:
            return "ALIAS_LINKED"
        return "CANONICAL"

    def entities(self, *, entity_type: str | None = None, source: str | None = None,
                 status: str | None = None, cross_source_only: bool = False,
                 has_ambiguous: bool = False, include_flagged: bool = False,
                 limit: int = 50, offset: int = 0) -> dict[str, Any]:
        with self._sf() as s:
            cands = s.execute(select(EntityResolutionCandidate)).scalars().all()
            handles = s.execute(select(EntitySourceHandle)).scalars().all()
        canon_handles: dict[str, list] = {}
        for h in handles:
            canon_handles.setdefault(h.canonical_entity_id, []).append(h)
        all_canon_ids = {c.candidate_canonical_entity_id for c in cands if c.candidate_canonical_entity_id}
        # ambiguous mentions per (entity_type, normalized_name)
        ambiguous_by_name: dict[tuple, int] = {}
        for c in cands:
            if c.resolution_status == R.AMBIGUOUS:
                k = (c.entity_type, c.normalized_name)
                ambiguous_by_name[k] = ambiguous_by_name.get(k, 0) + 1

        agg: dict[str, dict[str, Any]] = {}
        for c in cands:
            cid = c.candidate_canonical_entity_id
            if not cid:
                continue
            a = agg.setdefault(cid, {"canonical_entity_id": cid, "entity_type": c.entity_type,
                                     "canonical_name": c.raw_name, "sources": set(), "events": set(),
                                     "statuses": set(), "normalized_name": c.normalized_name,
                                     "last_observed": c.observed_at})
            a["sources"].add(c.source)
            a["statuses"].add(c.resolution_status)
            if c.canonical_event_id:
                a["events"].add(c.canonical_event_id)
            if c.observed_at and (a["last_observed"] is None or c.observed_at > a["last_observed"]):
                a["last_observed"] = c.observed_at

        rows: list[dict[str, Any]] = []
        for cid, a in agg.items():
            hs = canon_handles.get(cid, [])
            ambiguous_count = ambiguous_by_name.get((a["entity_type"], a["normalized_name"]), 0)
            istate = self._identity_state(cid, a["entity_type"], a["statuses"], all_canon_ids, len(a["sources"]))
            row = {
                "canonical_entity_id": cid, "canonical_name": a["canonical_name"],
                "entity_type": a["entity_type"], "source_handles": len(hs),
                "sources": sorted(a["sources"]), "linked_event_count": len(a["events"]),
                "linked_source_count": len(a["sources"]),
                "resolution_status": "RESOLVED" if "RESOLVED" in a["statuses"] else sorted(a["statuses"])[0],
                "ambiguous_candidate_count": ambiguous_count, "identity_state": istate,
                "last_observed": _iso(_aware(a["last_observed"])),
            }
            rows.append(row)

        # 5B.2.4 — product suppression: a canonical whose every candidate is quarantined/placeholder/
        # review-only never appears on normal product surfaces (kept for Advanced/debug via include_flagged).
        if not include_flagged:
            rows = [r for r in rows if r["resolution_status"] not in R.NON_PRODUCT_STATES]
        if entity_type:
            rows = [r for r in rows if r["entity_type"] == entity_type]
        if source:
            rows = [r for r in rows if source in r["sources"]]
        if status:
            rows = [r for r in rows if r["resolution_status"] == status]
        if cross_source_only:
            rows = [r for r in rows if r["linked_source_count"] > 1]
        if has_ambiguous:
            rows = [r for r in rows if r["ambiguous_candidate_count"] > 0]
        rows.sort(key=lambda r: (-r["linked_source_count"], -r["linked_event_count"], r["canonical_entity_id"]))
        total = len(rows)
        return {"count": total, "limit": limit, "offset": offset,
                "entities": rows[offset:offset + limit]}

    def quality_audit(self, *, limit: int = 100) -> dict[str, Any]:
        """Read-only data-quality audit of the canonical registry (Phase 5B.2.3).

        Classifies each canonical ARTIST/VENUE/ORGANIZER with the deterministic interpretation library and
        reports problem classes + a bounded dry-run manifest (proposed action, auto-safe vs review). Never
        mutates. ``auto_safe`` is reserved for exact placeholder phrases with no contradicting evidence."""
        from crawl_service.entity_resolution import compound as _C
        from crawl_service.entity_resolution import normalizers as _N
        from crawl_service.entity_resolution import placeholders as _P

        with self._sf() as s:
            cands = s.execute(select(EntityResolutionCandidate)).scalars().all()
        agg: dict[str, dict[str, Any]] = {}
        for c in cands:
            cid = c.candidate_canonical_entity_id
            if not cid:
                continue
            a = agg.setdefault(cid, {"id": cid, "type": c.entity_type, "name": c.raw_name,
                                     "sources": set()})
            a["sources"].add(c.source)
        # cross-type index: slug -> set of canonical types it exists as
        cross: dict[str, set[str]] = {}
        for a in agg.values():
            cross.setdefault(_N.slug(a["name"]), set()).add(a["type"])
        known_artists = frozenset(_N.normalize_artist(a["name"]).normalized
                                  for a in agg.values() if a["type"] == "ARTIST")

        counts: dict[str, int] = {}
        by_type: dict[str, dict[str, int]] = {"ARTIST": {}, "VENUE": {}, "ORGANIZER": {}}
        manifest: list[dict[str, Any]] = []

        def flag(a, problem, action, auto_safe, evidence):
            counts[problem] = counts.get(problem, 0) + 1
            by_type.setdefault(a["type"], {})[problem] = by_type.setdefault(a["type"], {}).get(problem, 0) + 1
            if len(manifest) < limit:
                manifest.append({"canonical_entity_id": a["id"], "entity_type": a["type"],
                                 "name": a["name"], "problem_class": problem, "proposed_action": action,
                                 "auto_safe": auto_safe, "requires_review": not auto_safe,
                                 "sources": sorted(a["sources"]), "evidence": evidence})

        clean = 0
        for a in agg.values():
            name, etype = a["name"], a["type"]
            ph = _P.classify_placeholder(name)
            slug = _N.slug(name)
            other_types = sorted(t for t in cross.get(slug, set()) if t != etype)
            if ph.is_placeholder:
                auto_safe = ph.reason in ("exact_placeholder_phrase", "kind_prefixed_placeholder")
                flag(a, "PLACEHOLDER_ENTITY", "suppress_from_product_and_relink_evidence", auto_safe,
                     {"placeholder_reason": ph.reason})
            elif other_types:
                flag(a, "CROSS_TYPE_CONFLICT", "review_role_conflict", False,
                     {"also_exists_as": other_types})
            elif etype == "ARTIST" and _C.parse_compound(name, known_names=known_artists).kind == _C.COMPOUND:
                parts = _C.parse_compound(name, known_names=known_artists).parts
                flag(a, "COMPOUND_ENTITY", "split_into_mentions_via_governance", False,
                     {"parts": parts})
            else:
                clean += 1

        return {"canonical_entities_audited": len(agg), "clean": clean,
                "counts_by_problem": counts, "counts_by_type": by_type,
                "manifest": manifest, "manifest_truncated": len(manifest) >= limit,
                "note": "read-only audit; no canonical data mutated. auto_safe items are exact placeholder "
                        "phrases eligible for deterministic suppression; everything else is review-gated."}

    # ---- review queue + governed corrections (5B.2.4) -------------------------------------------
    def review_queue(self, *, limit: int = 100) -> dict[str, Any]:
        """Live interpretation review items — mentions the gate flagged (REVIEW_REQUIRED / ROLE_CONFLICT /
        PLACEHOLDER), for which no canonical was created, awaiting operator adjudication."""
        with self._sf() as s:
            rows = s.execute(
                select(EntityResolutionCandidate)
                .where(EntityResolutionCandidate.resolution_status.in_(
                    (R.REVIEW_REQUIRED, R.ROLE_CONFLICT, R.PLACEHOLDER)))
                .order_by(EntityResolutionCandidate.updated_at.desc()).limit(limit)).scalars().all()
        items, counts = [], {}
        for c in rows:
            interp = (c.evidence or {}).get("interpretation") or {}
            counts[c.resolution_status] = counts.get(c.resolution_status, 0) + 1
            items.append({
                "candidate_id": c.id, "entity_type": c.entity_type, "raw_name": c.raw_name,
                "source": c.source, "canonical_event_id": c.canonical_event_id,
                "expected_role": interp.get("expected_role") or c.entity_type,
                "problem_class": c.resolution_status, "reason": c.reason_code,
                "created_at": _iso(_aware(c.created_at)), "evidence": interp})
        return {"count": len(items), "by_class": counts, "items": items,
                "note": "live interpretation-gated mentions awaiting review; no canonical was created."}

    def quality_metrics(self) -> dict[str, Any]:
        """Live integrity-flow metrics (5B.2.5): interpretation outcomes across all mentions, by type +
        source, plus open review items, oldest review age, and recent corrections. No fabricated trends."""
        now = datetime.now(UTC)
        with self._sf() as s:
            rows = s.execute(
                select(EntityResolutionCandidate.entity_type, EntityResolutionCandidate.resolution_status,
                       EntityResolutionCandidate.source, EntityResolutionCandidate.evidence,
                       EntityResolutionCandidate.raw_name, EntityResolutionCandidate.updated_at)).all()

        def _flow(st: str, ev: dict) -> str:
            interp = (ev or {}).get("interpretation") or {}
            if st in (R.RESOLVED, R.POSSIBLE_MATCH):
                return "resolved"
            if st == R.PLACEHOLDER:
                return "placeholder_suppressed"
            if st == R.QUARANTINED:
                return "quarantined"
            if st == R.ROLE_CONFLICT:
                return "role_conflict"
            if st == R.REVIEW_REQUIRED:
                return "ambiguous_compound" if interp.get("state") == "AMBIGUOUS_COMPOUND" else "review_required"
            if st == R.AMBIGUOUS:
                return "ambiguous"
            return "unresolved"

        flow: dict[str, int] = {}
        by_type: dict[str, dict[str, int]] = {}
        by_source: dict[str, dict[str, int]] = {}
        compound_split = clean_single = corrected = open_review = 0
        oldest_age_h: float | None = None
        recently: list[dict[str, Any]] = []
        for etype, st, src, ev, raw, upd in rows:
            b = _flow(st, ev or {})
            flow[b] = flow.get(b, 0) + 1
            by_type.setdefault(etype, {})[b] = by_type.setdefault(etype, {}).get(b, 0) + 1
            by_source.setdefault(src, {})[b] = by_source.setdefault(src, {}).get(b, 0) + 1
            if (ev or {}).get("split_from"):
                compound_split += 1
            elif b == "resolved":
                clean_single += 1
            corrs = (ev or {}).get("corrections")
            if corrs:
                corrected += 1
                last = corrs[-1]
                recently.append({"name": raw, "action": last.get("action"), "actor": last.get("actor"),
                                 "at": last.get("at")})
            if st in (R.REVIEW_REQUIRED, R.ROLE_CONFLICT):
                open_review += 1
                if upd is not None:
                    age = (now - _aware(upd)).total_seconds() / 3600.0
                    oldest_age_h = age if oldest_age_h is None else max(oldest_age_h, age)
        recently.sort(key=lambda r: r.get("at") or "", reverse=True)
        return {
            "mentions_processed": sum(flow.values()),
            "flow": {"clean_single": clean_single, "compound_split": compound_split, **flow},
            "by_type": by_type, "by_source": by_source,
            "open_review_items": open_review,
            "oldest_review_age_hours": round(oldest_age_h, 1) if oldest_age_h is not None else None,
            "operator_corrected": corrected, "ai_adjudicated": 0,
            "recently_corrected": recently[:10],
            "interpretation_method": "deterministic",  # AI adjudicator disabled
        }

    def quarantine_canonical(self, canonical_entity_id: str, *, reason: str, actor: str,
                             now: datetime | None = None) -> dict[str, Any]:
        """Governed suppression of a bad canonical (placeholder/junk). Sets its candidates to QUARANTINED,
        records evidence.quarantine + a history row per candidate. NEVER deletes a row or observation."""
        now = now or datetime.now(UTC)
        examined = quarantined = 0
        with self._sf() as s, s.begin():
            cands = s.execute(select(EntityResolutionCandidate).where(
                EntityResolutionCandidate.candidate_canonical_entity_id == canonical_entity_id)).scalars().all()
            examined = len(cands)
            for c in cands:
                prev = c.resolution_status
                ev = dict(c.evidence or {})
                ev["quarantine"] = {"reason": reason, "actor": actor, "at": now.isoformat(),
                                    "previous_status": prev}
                ev.setdefault("corrections", []).append({"action": "MARK_PLACEHOLDER", "actor": actor,
                                                          "reason": reason, "at": now.isoformat(),
                                                          "previous_status": prev})
                c.evidence = ev
                c.resolution_status = R.QUARANTINED
                c.updated_at = now
                s.add(EntityResolutionHistory(
                    id=_uuid(), candidate_id=c.id, previous_status=prev, new_status=R.QUARANTINED,
                    previous_canonical_entity_id=c.candidate_canonical_entity_id,
                    new_canonical_entity_id=c.candidate_canonical_entity_id, reason_code="QUARANTINE",
                    resolver_version=R.RESOLVER_VERSION, created_at=now))
                quarantined += 1
        return {"canonical_entity_id": canonical_entity_id, "candidates_examined": examined,
                "candidates_quarantined": quarantined, "actor": actor, "reason": reason,
                "evidence_preserved": True, "rows_deleted": 0}

    def apply_correction(self, *, action: str, actor: str, reason: str | None = None,
                         canonical_entity_id: str | None = None, candidate_id: str | None = None,
                         now: datetime | None = None) -> dict[str, Any]:
        """Narrowly-scoped governed correction (no arbitrary CRUD). Records actor/time/prev/new in
        evidence.corrections + a history row."""
        now = now or datetime.now(UTC)
        action = (action or "").upper()
        if action == "MARK_PLACEHOLDER":
            if not canonical_entity_id:
                raise ValueError("canonical_entity_id required for MARK_PLACEHOLDER")
            return self.quarantine_canonical(canonical_entity_id,
                                             reason=reason or "operator marked placeholder", actor=actor, now=now)
        if not candidate_id:
            raise ValueError("candidate_id required")
        with self._sf() as s, s.begin():
            c = s.get(EntityResolutionCandidate, candidate_id)
            if c is None:
                raise ValueError("candidate not found")
            prev = c.resolution_status
            ev = dict(c.evidence or {})
            if action == "CONFIRM_MULTI_ROLE":
                ev["multi_role_confirmed"] = True
                c.resolution_status = R.UNRESOLVED     # re-resolves next pass, gate now bypassed
                new = "LEGITIMATE_MULTI_ROLE"
            elif action == "REQUEUE_RESOLUTION":
                c.resolution_status, new = R.UNRESOLVED, R.UNRESOLVED
            elif action == "REJECT_MATCH":
                c.candidate_canonical_entity_id, c.resolution_status, new = None, R.UNRESOLVED, "REJECTED_MATCH"
            elif action == "CONFIRM_EXISTING_MATCH":
                c.resolution_status, new = R.RESOLVED, R.RESOLVED
            else:
                raise ValueError(f"unsupported action {action}")
            ev.setdefault("corrections", []).append({"action": action, "actor": actor, "reason": reason,
                                                     "at": now.isoformat(), "previous_status": prev})
            c.evidence = ev
            c.updated_at = now
            s.add(EntityResolutionHistory(
                id=_uuid(), candidate_id=c.id, previous_status=prev, new_status=c.resolution_status,
                previous_canonical_entity_id=c.candidate_canonical_entity_id,
                new_canonical_entity_id=c.candidate_canonical_entity_id,
                reason_code=f"CORRECTION:{action}", resolver_version=R.RESOLVER_VERSION, created_at=now))
        return {"candidate_id": candidate_id, "action": action, "actor": actor, "new_state": new,
                "audited": True}

    def entity_detail(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        with self._sf() as s:
            cands = s.execute(
                select(EntityResolutionCandidate).where(
                    EntityResolutionCandidate.candidate_canonical_entity_id == entity_id)
            ).scalars().all()
            handles = s.execute(
                select(EntitySourceHandle).where(
                    EntitySourceHandle.canonical_entity_id == entity_id)
            ).scalars().all()
            all_canon = {c.candidate_canonical_entity_id for c in
                         s.execute(select(EntityResolutionCandidate)).scalars().all()
                         if c.candidate_canonical_entity_id}
        if not cands and not handles:
            return None
        sources = sorted({c.source for c in cands})
        statuses = {c.resolution_status for c in cands}
        events = sorted({c.canonical_event_id for c in cands if c.canonical_event_id})
        name = cands[0].raw_name if cands else entity_id
        istate = self._identity_state(entity_id, entity_type, statuses, all_canon, len(sources))
        legacy = None
        if entity_type == "VENUE" and "--" in entity_id:
            base = entity_id.rsplit("--", 1)[0]
            if base in all_canon:
                legacy = base
        return {
            "canonical_entity_id": entity_id, "canonical_name": name, "entity_type": entity_type,
            "identity_state": istate, "legacy_projection_id": legacy,
            "sources": sources, "linked_events": events, "linked_event_count": len(events),
            "linked_source_count": len(sources),
            "source_handles": [{"source": h.source, "handle": h.source_entity_handle,
                                "confidence": h.confidence, "method": h.resolution_method,
                                "first_seen": _iso(_aware(h.first_seen)),
                                "last_seen": _iso(_aware(h.last_seen))} for h in handles],
            "candidates": [self._row_summary(c) for c in cands],
        }

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
