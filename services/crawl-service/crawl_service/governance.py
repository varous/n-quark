"""Governed entity-resolution commands (Admin Phase B).

The actual data mutations behind the admin BFF's governance commands. They reuse the Phase 3.1
pathways — the same canonical-id convention, source-handle registry, resolution history and graph
relationship writes — so no parallel entity-resolution system is created. Every command is explicit,
validated, non-destructive to source evidence, and returns refreshed state. Concurrency conflicts are
raised as GovernanceConflict; validation problems as GovernanceError.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from crawl_service.config import Settings, settings
from crawl_service.entity_resolution import normalizers as N
from crawl_service.entity_resolution.resolvers import _cid
from crawl_service.entity_resolution.service import _NODE_TYPE, _REL
from crawl_service.models import (
    EntityResolutionCandidate,
    EntityResolutionHistory,
    EntitySourceHandle,
    EntitySupersession,
)

GOV_VERSION = "governance-1"
RESOLVABLE = ("AMBIGUOUS", "POSSIBLE_MATCH", "UNRESOLVED", "REJECTED")
_ENTITY_PREFIX = {"ARTIST": "artist", "VENUE": "venue", "ORGANIZER": "organizer", "EVENT_SERIES": "series"}
_GENERIC_NAMES = (N.GENERIC_VENUE_NAMES | N.GENERIC_SERIES_TITLES
                  | frozenset({"events", "productions", "entertainment"}))


class GovernanceError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


class GovernanceConflict(GovernanceError):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class GovernanceService:
    def __init__(self, session_factory, graph_reader, graph_writer, config: Settings | None = None) -> None:
        self._sf = session_factory
        self._graph = graph_reader
        self._writer = graph_writer
        self._cfg = config or settings

    # ---- helpers --------------------------------------------------------------------------------
    def _candidate(self, s, candidate_id: str) -> EntityResolutionCandidate:
        c = s.get(EntityResolutionCandidate, candidate_id)
        if c is None:
            raise GovernanceError("CANDIDATE_NOT_FOUND", candidate_id)
        return c

    async def _node_exists(self, node_id: str) -> tuple[bool, str | None]:
        node, _ = await self._graph.get_event(node_id)
        if not node:
            return False, None
        return True, node.get("type")

    def _handle_owner(self, s, source: str, handle: str) -> str | None:
        return s.execute(
            select(EntitySourceHandle.canonical_entity_id).where(
                EntitySourceHandle.source == source, EntitySourceHandle.source_entity_handle == handle)
        ).scalar_one_or_none()

    def _history(self, s, cand_id, prev_status, new_status, prev_cid, new_cid, reason, now) -> None:
        s.add(EntityResolutionHistory(
            id=_uuid(), candidate_id=cand_id, previous_status=prev_status, new_status=new_status,
            previous_canonical_entity_id=prev_cid, new_canonical_entity_id=new_cid,
            reason_code=reason, resolver_version=GOV_VERSION, created_at=now))

    def _row(self, c: EntityResolutionCandidate) -> dict[str, Any]:
        return {"id": c.id, "entity_type": c.entity_type, "source": c.source,
                "source_record_id": c.source_record_id, "canonical_event_id": c.canonical_event_id,
                "handle": c.source_entity_handle, "raw_name": c.raw_name,
                "normalized_name": c.normalized_name,
                "canonical_entity_id": c.candidate_canonical_entity_id,
                "status": c.resolution_status, "reason": c.reason_code, "evidence": c.evidence}

    async def _write(self, nodes, edges) -> None:
        if nodes or edges:
            await self._writer.upsert_batch(nodes, edges)

    # ---- impact preview (no mutation) -----------------------------------------------------------
    async def preview(self, *, action: str, candidate_id: str | None = None,
                      canonical_entity_id: str | None = None, legacy_entity_id: str | None = None,
                      **_: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"action": action, "mutates_state": False,
                               "scheduler_metadata_change": False,
                               "duplicate_event_reconciliation_affected": False,
                               "source_evidence_retained": True}
        with self._sf() as s:
            if candidate_id:
                c = self._candidate(s, candidate_id)
                out["candidate"] = self._row(c)
                out["current_canonical_target"] = c.candidate_canonical_entity_id
                out["proposed_canonical_target"] = canonical_entity_id or c.candidate_canonical_entity_id
                conflicting = s.execute(
                    select(EntityResolutionCandidate.id, EntityResolutionCandidate.source,
                           EntityResolutionCandidate.candidate_canonical_entity_id)
                    .where(EntityResolutionCandidate.entity_type == c.entity_type,
                           EntityResolutionCandidate.normalized_name == c.normalized_name,
                           EntityResolutionCandidate.id != c.id)
                ).all()
                out["possible_conflicting_candidates"] = [
                    {"id": i, "source": src, "canonical": cid} for i, src, cid in conflicting]
                if c.candidate_canonical_entity_id:
                    out["events_affected"] = self._entity_events(s, c.candidate_canonical_entity_id)
                out["source_handles_affected"] = [c.source_entity_handle]
            target = canonical_entity_id
            if target:
                handles = s.execute(
                    select(EntitySourceHandle.source, EntitySourceHandle.source_entity_handle)
                    .where(EntitySourceHandle.canonical_entity_id == target)).all()
                out["target_existing_handles"] = [{"source": a, "handle": b} for a, b in handles]
                out["events_affected"] = self._entity_events(s, target)
            if legacy_entity_id:
                out["legacy_nodes_affected"] = [legacy_entity_id]
        if canonical_entity_id:
            exists, ntype = await self._node_exists(canonical_entity_id)
            out["target_exists"] = exists
            out["target_node_type"] = ntype
        return out

    def _entity_events(self, s, canonical_id: str) -> list[str]:
        rows = s.execute(
            select(EntityResolutionCandidate.canonical_event_id).where(
                EntityResolutionCandidate.candidate_canonical_entity_id == canonical_id,
                EntityResolutionCandidate.canonical_event_id.is_not(None))
        ).scalars().all()
        return sorted({r for r in rows if r})

    # ---- accept / link --------------------------------------------------------------------------
    async def accept(self, *, candidate_id: str, canonical_entity_id: str,
                     expected_status: str | None = None, reason_code: str = "MANUAL_ACCEPT",
                     require_exists: bool = True, decision_ref: str | None = None) -> dict[str, Any]:
        exists, ntype = await self._node_exists(canonical_entity_id) if require_exists else (True, None)
        with self._sf() as s, s.begin():
            c = self._candidate(s, candidate_id)
            if expected_status and c.resolution_status != expected_status:
                raise GovernanceConflict("STALE_PREVIEW",
                                         f"candidate is {c.resolution_status}, expected {expected_status}")
            if (c.resolution_status == "RESOLVED"
                    and c.candidate_canonical_entity_id == canonical_entity_id):
                return {"candidate": self._row(c), "idempotent": True,
                        "previous_status": c.resolution_status,
                        "previous_canonical_entity_id": c.candidate_canonical_entity_id}
            if require_exists and not exists:
                raise GovernanceError("TARGET_ENTITY_NOT_FOUND", canonical_entity_id)
            if ntype and ntype != _NODE_TYPE.get(c.entity_type):
                raise GovernanceError("ENTITY_TYPE_MISMATCH", f"{ntype} != {c.entity_type}")
            owner = self._handle_owner(s, c.source, c.source_entity_handle)
            if owner and owner != canonical_entity_id:
                raise GovernanceConflict("HANDLE_ALREADY_LINKED",
                                         f"{c.source_entity_handle} -> {owner}")
            prev_status, prev_cid = c.resolution_status, c.candidate_canonical_entity_id
            now = datetime.now(UTC)
            self._register_handle(s, c, canonical_entity_id, now, reason_code)
            c.resolution_status = "RESOLVED"
            c.candidate_canonical_entity_id = canonical_entity_id
            c.reason_code = reason_code
            c.resolver_version = GOV_VERSION
            c.updated_at = now
            self._history(s, c.id, prev_status, "RESOLVED", prev_cid, canonical_entity_id,
                          reason_code, now)
            row = self._row(c)
            ev_id, etype, raw = c.canonical_event_id, c.entity_type, c.raw_name
            handle, source = c.source_entity_handle, c.source
        await self._write(
            [{"id": canonical_entity_id, "type": _NODE_TYPE[etype], "properties": {"display_name": raw}},
             {"id": handle, "type": "source_handle", "properties": {"source": source, "raw_name": raw}}],
            [{"source": handle, "relationship": "IDENTIFIES", "target": canonical_entity_id,
              "properties": {"confidence": 1.0, "manual": True}}]
            + ([{"source": ev_id, "relationship": _REL[etype], "target": canonical_entity_id,
                 "properties": {"resolution_status": "RESOLVED", "manual": True}}] if ev_id else []))
        return {"candidate": row, "previous_status": prev_status,
                "previous_canonical_entity_id": prev_cid, "decision_ref": decision_ref}

    def _register_handle(self, s, c, canonical_entity_id, now, method) -> None:
        existing = s.execute(
            select(EntitySourceHandle).where(
                EntitySourceHandle.source == c.source,
                EntitySourceHandle.source_entity_handle == c.source_entity_handle)
        ).scalar_one_or_none()
        if existing is None:
            s.add(EntitySourceHandle(
                id=_uuid(), entity_type=c.entity_type, source=c.source,
                source_entity_handle=c.source_entity_handle,
                source_url=(c.evidence or {}).get("source_url"),
                canonical_entity_id=canonical_entity_id, confidence=1.0,
                resolution_method=method, first_seen=now, last_seen=now))
        else:
            existing.canonical_entity_id = canonical_entity_id
            existing.resolution_method = method
            existing.last_seen = now

    # ---- reject ---------------------------------------------------------------------------------
    def reject(self, *, candidate_id: str, reason_code: str, expected_status: str | None = None) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self._sf() as s, s.begin():
            c = self._candidate(s, candidate_id)
            if expected_status and c.resolution_status != expected_status:
                raise GovernanceConflict("STALE_PREVIEW", c.resolution_status)
            prev_status, prev_cid = c.resolution_status, c.candidate_canonical_entity_id
            c.resolution_status = "REJECTED"
            c.reason_code = reason_code
            c.updated_at = now
            self._history(s, c.id, prev_status, "REJECTED", prev_cid, None, reason_code, now)
            row = self._row(c)
        return {"candidate": row, "previous_status": prev_status,
                "previous_canonical_entity_id": prev_cid}

    def mark_unresolved(self, *, candidate_id: str, reason_code: str = "MANUAL_UNRESOLVED") -> dict[str, Any]:
        now = datetime.now(UTC)
        with self._sf() as s, s.begin():
            c = self._candidate(s, candidate_id)
            prev_status, prev_cid = c.resolution_status, c.candidate_canonical_entity_id
            c.resolution_status = "UNRESOLVED"
            c.candidate_canonical_entity_id = None
            c.reason_code = reason_code
            c.updated_at = now
            self._history(s, c.id, prev_status, "UNRESOLVED", prev_cid, None, reason_code, now)
            row = self._row(c)
        return {"candidate": row, "previous_status": prev_status,
                "previous_canonical_entity_id": prev_cid}

    # ---- create canonical entity ----------------------------------------------------------------
    async def create_entity(self, *, entity_type: str, canonical_name: str, candidate_id: str,
                            city: str | None = None, region_id: str | None = None,
                            organizer: str | None = None, reason_code: str = "MANUAL_CREATE",
                            decision_ref: str | None = None) -> dict[str, Any]:
        if entity_type not in _ENTITY_PREFIX:
            raise GovernanceError("BAD_ENTITY_TYPE", entity_type)
        norm = N.slug(canonical_name)
        if not norm:
            raise GovernanceError("EMPTY_NAME")
        base = N._base(canonical_name)  # noqa: SLF001 — shared normalizer
        if base in _GENERIC_NAMES and entity_type in ("VENUE", "EVENT_SERIES", "ORGANIZER"):
            raise GovernanceError("GENERIC_NAME", f"'{canonical_name}' is too generic to create")
        if entity_type == "VENUE" and not city:
            raise GovernanceError("VENUE_REQUIRES_CITY")
        if entity_type == "VENUE":
            cid = _cid("venue", norm.replace("-", " "), city)
        elif entity_type == "EVENT_SERIES" and organizer:
            cid = _cid("series", norm.replace("-", " "), organizer)
        else:
            cid = _cid(_ENTITY_PREFIX[entity_type], norm.replace("-", " "))
        # creation resolves the originating candidate onto the new entity (the node may not exist yet)
        result = await self.accept(candidate_id=candidate_id, canonical_entity_id=cid,
                                   reason_code=reason_code, require_exists=False, decision_ref=decision_ref)
        props: dict[str, Any] = {"display_name": canonical_name}
        if city:
            props["city"] = city
        if region_id:
            props["region_id"] = region_id
        await self._write([{"id": cid, "type": _NODE_TYPE[entity_type], "properties": props}], [])
        result["created_canonical_entity_id"] = cid
        return result

    # ---- supersede legacy projection (ADMIN) ----------------------------------------------------
    async def supersede_legacy(self, *, entity_type: str, legacy_entity_id: str,
                               canonical_entity_id: str, decision_ref: str | None = None) -> dict[str, Any]:
        if legacy_entity_id == canonical_entity_id:
            raise GovernanceError("SAME_NODE")
        exists, _ = await self._node_exists(canonical_entity_id)
        if not exists:
            raise GovernanceError("TARGET_ENTITY_NOT_FOUND", canonical_entity_id)
        now = datetime.now(UTC)
        with self._sf() as s, s.begin():
            existing = s.execute(
                select(EntitySupersession).where(
                    EntitySupersession.legacy_entity_id == legacy_entity_id)
            ).scalar_one_or_none()
            if existing is not None and existing.active and existing.canonical_entity_id != canonical_entity_id:
                raise GovernanceConflict("LEGACY_ALREADY_SUPERSEDED",
                                         f"{legacy_entity_id} -> {existing.canonical_entity_id}")
            if existing is None:
                s.add(EntitySupersession(
                    id=_uuid(), entity_type=entity_type, legacy_entity_id=legacy_entity_id,
                    canonical_entity_id=canonical_entity_id, relationship="SUPERSEDED_BY",
                    decision_ref=decision_ref, active=True, created_at=now, updated_at=now))
            else:
                existing.canonical_entity_id = canonical_entity_id
                existing.active = True
                existing.decision_ref = decision_ref
                existing.updated_at = now
        # non-destructive: preserve the legacy node + its edges; add a SUPERSEDED_BY edge.
        await self._write([], [{"source": legacy_entity_id, "relationship": "SUPERSEDED_BY",
                                "target": canonical_entity_id, "properties": {"manual": True}}])
        return {"legacy_entity_id": legacy_entity_id, "canonical_entity_id": canonical_entity_id,
                "relationship": "SUPERSEDED_BY", "decision_ref": decision_ref}

    async def unsupersede(self, *, legacy_entity_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self._sf() as s, s.begin():
            existing = s.execute(
                select(EntitySupersession).where(
                    EntitySupersession.legacy_entity_id == legacy_entity_id)
            ).scalar_one_or_none()
            if existing is None or not existing.active:
                return {"legacy_entity_id": legacy_entity_id, "already_inactive": True}
            existing.active = False
            existing.updated_at = now
            canonical = existing.canonical_entity_id
        await self._write([], [{"source": legacy_entity_id, "relationship": "SUPERSEDED_BY",
                                "target": canonical, "properties": {"active": False}}])
        return {"legacy_entity_id": legacy_entity_id, "reactivated_as_separate": True}

    # ---- correct event series -------------------------------------------------------------------
    async def correct_series(self, *, event_id: str, mode: str, series_id: str | None = None,
                             series_name: str | None = None, organizer: str | None = None,
                             prev_series_id: str | None = None,
                             decision_ref: str | None = None) -> dict[str, Any]:
        """mode: UNLINK (supersede the current PART_OF_SERIES), LINK (to series_id),
        CREATE (a valid series from series_name+organizer, then link)."""
        edges: list[dict] = []
        nodes: list[dict] = []
        result: dict[str, Any] = {"event_id": event_id, "mode": mode}
        if prev_series_id:
            # supersede the previous relationship non-destructively (mark REVERSED, don't delete)
            edges.append({"source": event_id, "relationship": "PART_OF_SERIES", "target": prev_series_id,
                          "properties": {"resolution_status": "SUPERSEDED", "manual": True}})
        if mode == "UNLINK":
            if not prev_series_id:
                raise GovernanceError("NO_SERIES_TO_UNLINK")
            result["superseded"] = prev_series_id
        elif mode in ("LINK", "CREATE"):
            target = series_id
            if mode == "CREATE":
                if not series_name:
                    raise GovernanceError("SERIES_NAME_REQUIRED")
                base = N._base(series_name)  # noqa: SLF001
                if base in N.GENERIC_SERIES_TITLES and not organizer:
                    raise GovernanceError("GENERIC_SERIES_REQUIRES_ORGANIZER")
                target = _cid("series", base, organizer) if organizer else _cid("series", base)
                nodes.append({"id": target, "type": "event_series",
                              "properties": {"display_name": series_name}})
                result["created_canonical_entity_id"] = target
            if not target:
                raise GovernanceError("SERIES_TARGET_REQUIRED")
            exists, ntype = await self._node_exists(target) if mode == "LINK" else (True, "event_series")
            if mode == "LINK" and not exists:
                raise GovernanceError("TARGET_ENTITY_NOT_FOUND", target)
            edges.append({"source": event_id, "relationship": "PART_OF_SERIES", "target": target,
                          "properties": {"resolution_status": "RESOLVED", "manual": True}})
            result["linked_series"] = target
        else:
            raise GovernanceError("BAD_MODE", mode)
        await self._write(nodes, edges)
        return result

    # ---- reversal of an accept/link -------------------------------------------------------------
    async def reverse_accept(self, *, candidate_id: str, restore_status: str | None,
                             restore_canonical: str | None, remove_handle: bool = False) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self._sf() as s, s.begin():
            c = self._candidate(s, candidate_id)
            prev_status, prev_cid = c.resolution_status, c.candidate_canonical_entity_id
            handle, source, etype = c.source_entity_handle, c.source, c.entity_type
            ev_id, applied_cid = c.canonical_event_id, c.candidate_canonical_entity_id
            c.resolution_status = restore_status or "UNRESOLVED"
            c.candidate_canonical_entity_id = restore_canonical
            c.reason_code = "REVERSED"
            c.updated_at = now
            self._history(s, c.id, prev_status, c.resolution_status, prev_cid,
                          restore_canonical, "REVERSED", now)
            if remove_handle and not restore_canonical:
                hrow = s.execute(
                    select(EntitySourceHandle).where(
                        EntitySourceHandle.source == source,
                        EntitySourceHandle.source_entity_handle == handle)
                ).scalar_one_or_none()
                if hrow is not None:
                    s.delete(hrow)
            elif restore_canonical:
                self._register_handle(s, c, restore_canonical, now, "REVERSED")
            row = self._row(c)
        # non-destructive graph: mark the applied event relationship REVERSED (edges are never deleted)
        if ev_id and applied_cid:
            await self._write([], [{"source": ev_id, "relationship": _REL[etype], "target": applied_cid,
                                    "properties": {"resolution_status": "REVERSED", "manual": True}}])
        return {"candidate": row, "restored_status": c_status_of(row)}

    # ---- counts (dedupe aliases/superseded) -----------------------------------------------------
    async def governance_counts(self) -> dict[str, Any]:
        with self._sf() as s:
            canon_ids = {r for r in s.execute(
                select(EntityResolutionCandidate.candidate_canonical_entity_id).where(
                    EntityResolutionCandidate.resolution_status == "RESOLVED",
                    EntityResolutionCandidate.candidate_canonical_entity_id.is_not(None))
            ).scalars().all() if r}
            superseded = s.execute(
                select(EntitySupersession.legacy_entity_id, EntitySupersession.canonical_entity_id)
                .where(EntitySupersession.active.is_(True))
            ).all()
            superseded_ids = {a for a, _ in superseded}
            supersede_count = s.execute(
                select(func.count()).select_from(EntitySupersession)
                .where(EntitySupersession.active.is_(True))).scalar() or 0
        # canonical entities minus any that are themselves superseded legacy ids
        canonical_resolved = len(canon_ids - superseded_ids)
        return {"canonical_resolved_entities": canonical_resolved,
                "legacy_superseded_nodes": supersede_count,
                "superseded_legacy_ids": sorted(superseded_ids)}

    def superseded_map(self) -> dict[str, str]:
        with self._sf() as s:
            return {a: b for a, b in s.execute(
                select(EntitySupersession.legacy_entity_id, EntitySupersession.canonical_entity_id)
                .where(EntitySupersession.active.is_(True))).all()}


def c_status_of(row: dict) -> str:
    return str(row.get("status", ""))
