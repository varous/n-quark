"""AdminService — aggregates the existing internal service APIs into frontend read models.

Read-only except for the narrow, audited operational actions. Every method degrades gracefully when a
downstream is unavailable/disabled (returns partial data with an `unavailable` marker), never a 500.
No canonical data is copied into a frontend store — this only reshapes live reads.
"""

from __future__ import annotations

from typing import Any

from api_gateway.admin.gateway_client import Down, DownstreamGateway
from api_gateway.config import settings

CRAWL, GRAPH = "crawl", "graph"

# Epistemic labels the console uses (state is never communicated by colour alone in the UI).
EPISTEMIC = ("Observed", "Derived", "Resolved", "Estimated", "Unknown", "Ambiguous",
             "Conflicting", "Stale", "Failed")

STALE_GAP_HOURS = 48


def _page(items: list, limit: int, offset: int) -> list:
    return items[offset:offset + limit]


class AdminService:
    def __init__(self, gateway: DownstreamGateway | None = None) -> None:
        self.gw = gateway or DownstreamGateway()

    # ---- dashboard ------------------------------------------------------------------------------
    async def dashboard(self) -> dict[str, Any]:
        cov = await self.gw.get(CRAWL, "/v1/internal/capture-schedule", params={"limit": 500})
        ent = await self.gw.get(CRAWL, "/v1/internal/entity-resolution/coverage")
        jobs = await self.gw.get(CRAWL, "/v1/internal/capture-schedule/jobs", params={"limit": 200})

        tracked = (cov.data or {}).get("events", []) if cov.ok else []
        job_list = (jobs.data or {}).get("jobs", []) if jobs.ok else []
        et = (ent.data or {}).get("by_entity_type", {}) if ent.ok else {}

        active = [t for t in tracked if t.get("tracking_status") == "ACTIVE"]
        stale = [t for t in tracked if (t.get("capture_gap_hours") or 0) >= STALE_GAP_HOURS]
        three_plus = [t for t in tracked if (t.get("distinct_state_count") or 0) >= 3]
        failed_jobs = [j for j in job_list if j.get("status") in ("FAILED_RETRYABLE", "FAILED_TERMINAL")]

        def _sum(key: str) -> int:
            return sum(t.get(key, 0) or 0 for t in tracked)

        artist = et.get("ARTIST", {})
        cards = {
            "active_tracked_events": len(active),
            "captures_total": _sum("capture_count"),
            "events_with_3plus_states": len(three_plus),
            "transitions_total": _sum("transition_count"),
            "resolved_artist_rate": artist.get("resolution_rate", 0.0),
            "ambiguous_entity_candidates": sum(v.get("ambiguous_mentions", 0) for v in et.values()),
            "unresolved_entity_candidates": sum(v.get("unresolved_mentions", 0) for v in et.values()),
            "stale_tracked_events": len(stale),
            "failed_capture_jobs": len(failed_jobs),
            "cross_source_entities": sum(v.get("cross_source_canonical_entities", 0) for v in et.values()),
        }
        sources = {}
        for src in ("boshow", "district"):
            st = [t for t in tracked if t.get("source") == src]
            se = ((await self.gw.get(CRAWL, "/v1/internal/entity-resolution/coverage",
                                     params={"source": src})).data or {}).get("by_entity_type", {})
            sources[src] = {
                "tracked_events": len(st),
                "distinct_states": sum(t.get("distinct_state_count", 0) or 0 for t in st),
                "transitions": sum(t.get("transition_count", 0) or 0 for t in st),
                "resolved_artists": se.get("ARTIST", {}).get("resolved_mentions", 0),
                "resolved_venues": se.get("VENUE", {}).get("resolved_mentions", 0),
                "resolved_organizers": se.get("ORGANIZER", {}).get("resolved_mentions", 0),
                "series_links": se.get("EVENT_SERIES", {}).get("resolved_mentions", 0),
                "ambiguous": sum(v.get("ambiguous_mentions", 0) for v in se.values()),
                "unresolved": sum(v.get("unresolved_mentions", 0) for v in se.values()),
            }
        queues = {
            "recent_capture_failures": [self._job_link(j) for j in failed_jobs[:10]],
            "stale_events": [self._event_link(t) for t in stale[:10]],
            "events_without_geography": [self._event_link(t) for t in tracked
                                         if not t.get("city")][:10],
        }
        return {"cards": cards, "sources": sources, "attention_queues": queues,
                "downstream": {"crawl": cov.available, "entity_resolution": ent.ok}}

    @staticmethod
    def _event_link(t: dict) -> dict:
        return {"canonical_event_id": t.get("canonical_event_id"), "source": t.get("source"),
                "source_record_id": t.get("source_record_id"),
                "capture_gap_hours": t.get("capture_gap_hours"),
                "last_capture_status": t.get("last_capture_status")}

    @staticmethod
    def _job_link(j: dict) -> dict:
        return {"job_id": j.get("id"), "source": j.get("source"), "status": j.get("status"),
                "canonical_event_id": j.get("canonical_event_id"), "result_code": j.get("result_code")}

    # ---- sources --------------------------------------------------------------------------------
    async def sources(self) -> dict[str, Any]:
        d = await self.dashboard()
        return {"sources": [{"source": k, **v} for k, v in d["sources"].items()]}

    async def source_detail(self, source: str) -> dict[str, Any]:
        cov = await self.gw.get(CRAWL, "/v1/internal/capture-schedule", params={"source": source, "limit": 500})
        ent = await self.gw.get(CRAWL, "/v1/internal/entity-resolution/coverage", params={"source": source})
        tracked = (cov.data or {}).get("events", []) if cov.ok else []
        return {"source": source, "tracked_events": len(tracked),
                "entity_coverage": (ent.data or {}).get("by_entity_type", {}) if ent.ok else {},
                "events": [self._event_link(t) for t in tracked[:100]]}

    # ---- source / crawler diagnostics -----------------------------------------------------------
    async def source_diagnostics(self, source: str) -> dict[str, Any]:
        cov = await self.gw.get(CRAWL, "/v1/internal/capture-schedule",
                                params={"source": source, "limit": 500})
        jobs = await self.gw.get(CRAWL, "/v1/internal/capture-schedule/jobs",
                                 params={"source": source, "limit": 200})
        ent = await self.gw.get(CRAWL, "/v1/internal/entity-resolution/coverage",
                                params={"source": source})
        tracked = (cov.data or {}).get("events", []) if cov.ok else []
        job_list = (jobs.data or {}).get("jobs", []) if jobs.ok else []
        et = (ent.data or {}).get("by_entity_type", {}) if ent.ok else {}

        succeeded = [j for j in job_list if j.get("status") == "SUCCEEDED"]
        failed = [j for j in job_list if str(j.get("status", "")).startswith("FAILED")]
        terminal = [j for j in job_list if j.get("status") == "FAILED_TERMINAL"]
        finished = len(succeeded) + len(failed)
        # failure classification by result/error code
        failure_classes: dict[str, int] = {}
        for j in failed:
            code = j.get("last_error_code") or j.get("result_code") or "UNKNOWN"
            failure_classes[code] = failure_classes.get(code, 0) + 1
        # last successful capture across the source
        last_success = max((t.get("last_success_at") for t in tracked if t.get("last_success_at")),
                           default=None)
        gaps = [t.get("capture_gap_hours") for t in tracked if t.get("capture_gap_hours") is not None]
        avg_gap = round(sum(gaps) / len(gaps), 2) if gaps else None

        # field presence / validity across the source (placeholders count as present-but-invalid)
        present_geo = [t for t in tracked if t.get("city")]
        placeholder_geo = [t for t in tracked
                           if t.get("city") and self._is_placeholder(str(t.get("city")))]
        return {
            "source": source,
            "tracked_events": len(tracked),
            "last_successful_capture": last_success,
            "capture_success_rate": round(len(succeeded) / finished, 3) if finished else None,
            "jobs_total": len(job_list), "jobs_succeeded": len(succeeded), "jobs_failed": len(failed),
            "jobs_failed_terminal": len(terminal),
            "failure_classifications": failure_classes,
            "parser_failures": sum(1 for j in failed
                                   if "PARSE" in str(j.get("last_error_code") or "").upper()),
            "average_capture_gap_hours": avg_gap,
            "stale_events": sum(1 for t in tracked
                                if (t.get("capture_gap_hours") or 0) >= STALE_GAP_HOURS),
            "events_with_multiple_states": sum(1 for t in tracked
                                               if (t.get("distinct_state_count") or 0) > 1),
            "events_with_transitions": sum(1 for t in tracked
                                           if (t.get("transition_count") or 0) > 0),
            # field: present vs valid vs specific vs resolved (geography as the worked example)
            "geography": {
                "present": len(present_geo),
                "valid": len(present_geo) - len(placeholder_geo),
                "placeholder": len(placeholder_geo),
                "missing": len(tracked) - len(present_geo),
            },
            "entity_resolution": {
                etype: {"resolved": v.get("resolved_mentions", 0),
                        "ambiguous": v.get("ambiguous_mentions", 0),
                        "unresolved": v.get("unresolved_mentions", 0)}
                for etype, v in et.items()
            },
            "available": cov.available,
        }

    # Placeholder detector — deterministic, matches the known live placeholder patterns (never a match
    # is fabricated; this only flags obviously non-specific values so field *validity* is honest).
    _PLACEHOLDERS = ("mutiple cities", "multiple cities", "various", "tba", "tbd", "n/a", "india")

    @classmethod
    def _is_placeholder(cls, value: str) -> bool:
        v = value.strip().lower()
        return any(p == v or p in v for p in cls._PLACEHOLDERS)

    # ---- events ---------------------------------------------------------------------------------
    # Bound on how many tracked events we hydrate with graph/entity reads for text/date/resolution
    # filtering, so a large catalogue never fans out unboundedly.
    EVENT_HYDRATE_CAP = 250

    async def events(self, *, source: str | None = None, stale_only: bool = False,
                     has_transitions: bool = False, q: str | None = None, city: str | None = None,
                     date_from: str | None = None, date_to: str | None = None,
                     capture_state: str | None = None, resolution_status: str | None = None,
                     limit: int = 25, offset: int = 0) -> dict[str, Any]:
        cov = await self.gw.get(CRAWL, "/v1/internal/capture-schedule",
                                params={"limit": 500, **({"source": source} if source else {})})
        tracked = (cov.data or {}).get("events", []) if cov.ok else []
        # cheap tracked-level filters first
        if stale_only:
            tracked = [t for t in tracked if (t.get("capture_gap_hours") or 0) >= STALE_GAP_HOURS]
        if has_transitions:
            tracked = [t for t in tracked if (t.get("transition_count") or 0) > 0]
        if capture_state:
            cs = capture_state.upper()
            tracked = [t for t in tracked if (t.get("last_capture_status") or "").upper() == cs]

        needs_hydration = bool(q or city or date_from or date_to or resolution_status)
        capped = False
        if needs_hydration and len(tracked) > self.EVENT_HYDRATE_CAP:
            tracked, capped = tracked[: self.EVENT_HYDRATE_CAP], True

        rows = [await self._hydrate_event(t, deep=needs_hydration) for t in tracked] \
            if needs_hydration else [self._shallow_event(t) for t in tracked]

        if needs_hydration:
            ql = (q or "").strip().lower()
            cl = (city or "").strip().lower()
            rs = (resolution_status or "").strip().upper()
            rows = [
                r for r in rows
                if (not ql or ql in (r.get("_blob") or ""))
                and (not cl or cl in (r.get("city") or "").lower())
                and (not date_from or (r.get("starts_at") or "") >= date_from)
                and (not date_to or (r.get("starts_at") or "9999") <= date_to)
                and (not rs or (r.get("resolution_status") or "") == rs)
            ]
        for r in rows:
            r.pop("_blob", None)
        total = len(rows)
        return {"count": total, "limit": limit, "offset": offset,
                "events": _page(rows, limit, offset), "hydrated": needs_hydration,
                "capped": capped, "available": cov.available}

    @staticmethod
    def _shallow_event(t: dict) -> dict[str, Any]:
        return {
            "canonical_event_id": t.get("canonical_event_id"), "title": None, "source": t.get("source"),
            "source_record_id": t.get("source_record_id"), "city": t.get("city"),
            "starts_at": None, "tracking_status": t.get("tracking_status"),
            "last_capture_status": t.get("last_capture_status"),
            "state_count": t.get("distinct_state_count"), "transition_count": t.get("transition_count"),
            "capture_gap_hours": t.get("capture_gap_hours"),
            "enrichment_status": t.get("enrichment_status"), "resolution_status": None,
            "stale": (t.get("capture_gap_hours") or 0) >= STALE_GAP_HOURS,
        }

    async def _hydrate_event(self, t: dict, *, deep: bool) -> dict[str, Any]:
        row = self._shallow_event(t)
        cid = t.get("canonical_event_id")
        title = organizer = None
        names: list[str] = []
        if cid:
            node = await self.gw.get(GRAPH, f"/v1/graph/nodes/{cid}")
            props = (node.data or {}).get("properties", {}) if node.ok else {}
            title, organizer = props.get("display_name"), props.get("organizer")
            row["title"] = title
            row["city"] = props.get("city") or row["city"]
            row["starts_at"] = props.get("starts_at")
            resolved = await self.gw.get(CRAWL, f"/v1/internal/events/{cid}/resolved-entities")
            ents = (resolved.data or {}).get("entities", []) if resolved.ok else []
            names = [str(e.get("raw_name")) for e in ents if e.get("raw_name")]
            row["resolution_status"] = self._event_resolution_status(ents)
        blob = " ".join(str(x) for x in (title, organizer, row["city"], t.get("source_record_id"),
                                         cid, *names) if x).lower()
        row["_blob"] = blob
        return row

    @staticmethod
    def _event_resolution_status(entities: list[dict]) -> str:
        statuses = {str(e.get("status") or "").upper() for e in entities}
        if not statuses:
            return "NONE"
        for worst in ("CONFLICT", "AMBIGUOUS", "UNRESOLVED", "POSSIBLE_MATCH", "PARTIAL"):
            if worst in statuses:
                return worst
        return "RESOLVED"

    async def event_detail(self, event_id: str) -> dict[str, Any]:
        node = await self.gw.get(GRAPH, f"/v1/graph/nodes/{event_id}")
        neigh = await self.gw.get(GRAPH, f"/v1/graph/nodes/{event_id}/neighbors", params={"direction": "out"})
        resolved = await self.gw.get(CRAWL, f"/v1/internal/events/{event_id}/resolved-entities")
        recs = await self.gw.get(CRAWL, f"/v1/internal/events/{event_id}/source-records")
        props = (node.data or {}).get("properties", {}) if node.ok else {}
        return {
            "canonical_event_id": event_id,
            "current_view": {
                "title": props.get("display_name"), "starts_at": props.get("starts_at"),
                "city": props.get("city"), "organizer": props.get("organizer"),
                "price_min": props.get("price_min"), "currency": props.get("currency"),
                "fill_ratio": props.get("fill_ratio"), "source": props.get("source"),
                "source_url": props.get("source_url"), "epistemic": "Observed" if node.ok else "Unknown",
            },
            "relationships": self._relationships((neigh.data or {}).get("neighbors", []) if neigh.ok else []),
            "resolved_entities": (resolved.data or {}).get("entities", []) if resolved.ok else [],
            "source_records": (recs.data or {}).get("represented_by", []) if recs.ok else [],
            "available": node.available,
        }

    @staticmethod
    def _relationships(neighbors: list[dict]) -> list[dict]:
        out = []
        for nb in neighbors:
            node = nb.get("node") or {}
            props = node.get("properties") or {}
            out.append({
                "relationship": nb.get("relationship"), "canonical_target": node.get("id"),
                "target_name": props.get("display_name"), "target_type": node.get("type"),
                "confidence": props.get("confidence"), "resolution_status": props.get("resolution_status"),
            })
        return out

    async def event_timeline(self, event_id: str, *, raw: bool = False) -> dict[str, Any]:
        sl = await self.gw.get(GRAPH, f"/v1/internal/events/{event_id}/shadow-ledger",
                               params={"trace": True})
        if not sl.ok:
            return {"canonical_event_id": event_id, "available": sl.available, "transitions": [], "states": []}
        data = sl.data or {}
        return {"canonical_event_id": event_id, "available": True,
                "current": data.get("current"),
                "transitions": data.get("transitions", []), "states": data.get("states", []),
                "trace": data.get("trace")}

    async def event_evidence(self, event_id: str) -> dict[str, Any]:
        enr = await self.gw.get(CRAWL, f"/v1/internal/events/{event_id}/enrichment")
        if not enr.ok:
            return {"canonical_event_id": event_id, "available": enr.available,
                    "resolved_fields": {}, "candidates": []}
        return {"canonical_event_id": event_id, "available": True, **(enr.data or {})}

    # ---- entities -------------------------------------------------------------------------------
    async def entities(self, **filters) -> dict[str, Any]:
        params = {k: v for k, v in filters.items() if v not in (None, False)}
        r = await self.gw.get(CRAWL, "/v1/internal/entity-resolution/entities", params=params)
        if not r.ok:
            return {"count": 0, "entities": [], "available": r.available}
        return {**(r.data or {}), "available": True}

    async def data_quality(self, *, limit: int = 200) -> dict[str, Any]:
        """Canonical data-quality audit (5B.2.3): placeholder/compound/cross-type problems + dry-run
        manifest. Read-only; degrades gracefully."""
        r = await self.gw.get(CRAWL, "/v1/internal/entity-resolution/quality-audit",
                              params={"limit": limit})
        if not r.ok:
            return {"available": False}
        return {"available": True, **(r.data or {})}

    async def review_queue(self, *, limit: int = 100) -> dict[str, Any]:
        """Live interpretation review items (5B.2.4). Read-only; degrades gracefully."""
        r = await self.gw.get(CRAWL, "/v1/internal/entity-resolution/review-queue",
                              params={"limit": limit})
        if not r.ok:
            return {"available": False, "items": []}
        return {"available": True, **(r.data or {})}

    async def apply_correction(self, *, action: str, actor: str, reason: str | None = None,
                               canonical_entity_id: str | None = None,
                               candidate_id: str | None = None) -> dict[str, Any]:
        """Governed data-quality correction (5B.2.4) — forwards the authenticated operator as `actor`."""
        body = {"action": action, "actor": actor, "reason": reason,
                "canonical_entity_id": canonical_entity_id, "candidate_id": candidate_id}
        r = await self.gw.post(CRAWL, "/v1/internal/entity-resolution/correct", json=body)
        if r.ok:
            return {"ok": True, **(r.data if isinstance(r.data, dict) else {})}
        detail = "correction failed"
        if isinstance(r.data, dict):
            detail = str(r.data.get("detail") or detail)
        from fastapi import HTTPException
        raise HTTPException(r.status if r.available and r.status in range(400, 600) else 502, detail=detail)

    async def entity_detail(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        r = await self.gw.get(CRAWL, f"/v1/internal/entity-resolution/entities/{entity_type}/{entity_id}")
        handles = await self.gw.get(CRAWL, f"/v1/internal/entities/{entity_type}/{entity_id}/source-handles")
        if not r.ok:
            return {"canonical_entity_id": entity_id, "available": r.available, "status": r.status}
        data = dict(r.data or {})
        if handles.ok:
            data["source_handles"] = (handles.data or {}).get("handles", data.get("source_handles", []))
        return {**data, "available": True}

    # ---- resolution queue -----------------------------------------------------------------------
    # The inspection-first diagnostic queues (Phase C). Values match the resolver's own statuses.
    QUEUE_STATES = ("AMBIGUOUS", "UNRESOLVED", "POSSIBLE_MATCH", "CONFLICT", "LOW_CONFIDENCE")

    async def resolution_queue(self, *, entity_type: str | None = None, source: str | None = None,
                               status: str | None = None, limit: int = 50) -> dict[str, Any]:
        # fetch a bounded superset, then filter by diagnostic status client-side
        params: dict[str, Any] = {"limit": max(limit, 200)}
        if entity_type:
            params["entity_type"] = entity_type
        if source:
            params["source"] = source
        r = await self.gw.get(CRAWL, "/v1/internal/entity-resolution/unresolved", params=params)
        if not r.ok:
            return {"count": 0, "items": [], "by_status": {}, "available": r.available}
        items = list((r.data or {}).get("items", []))
        by_status: dict[str, int] = {}
        for it in items:
            st = str(it.get("status") or "").upper()
            by_status[st] = by_status.get(st, 0) + 1
        if status:
            st = status.upper()
            items = [it for it in items if str(it.get("status") or "").upper() == st]
        return {"count": len(items), "items": items[:limit], "by_status": by_status,
                "states": list(self.QUEUE_STATES), "available": True}

    async def candidate_detail(self, candidate_id: str) -> dict[str, Any]:
        r = await self.gw.get(CRAWL, f"/v1/internal/entity-resolution/candidates/{candidate_id}")
        return {"available": r.available, "status": r.status, **(r.data or {})} if r.data else \
            {"available": r.available, "status": r.status}

    # ---- capture jobs ---------------------------------------------------------------------------
    async def capture_jobs(self, **filters) -> dict[str, Any]:
        params = {k: v for k, v in filters.items() if v not in (None, False)}
        r = await self.gw.get(CRAWL, "/v1/internal/capture-schedule/jobs", params=params)
        if not r.ok:
            return {"count": 0, "jobs": [], "available": r.available}
        return {**(r.data or {}), "available": True}

    async def capture_job(self, job_id: str) -> dict[str, Any]:
        r = await self.gw.get(CRAWL, f"/v1/internal/capture-schedule/jobs/{job_id}")
        return {"available": r.available, "status": r.status, **(r.data or {})} if r.data else \
            {"available": r.available, "status": r.status}

    # ---- bounded graph explorer -----------------------------------------------------------------
    async def subgraph(self, root: str, *, depth: int, rel_types: set[str] | None = None) -> dict[str, Any]:
        max_nodes = settings.admin_graph_max_nodes
        depth = min(depth, settings.admin_graph_max_depth)
        seen: dict[str, dict] = {}
        edges: list[dict] = []
        edge_seen: set[tuple] = set()
        frontier = [root]
        capped = False
        for _ in range(depth + 1):
            nxt = []
            for nid in frontier:
                if nid in seen:
                    continue
                if len(seen) >= max_nodes:
                    capped = True
                    break
                node = await self.gw.get(GRAPH, f"/v1/graph/nodes/{nid}")
                if node.ok and node.data:
                    seen[nid] = {"id": nid, "type": node.data.get("type"),
                                 "label": (node.data.get("properties") or {}).get("display_name") or nid}
                else:
                    seen[nid] = {"id": nid, "type": "unknown", "label": nid}
                nb = await self.gw.get(GRAPH, f"/v1/graph/nodes/{nid}/neighbors", params={"direction": "both"})
                for n in ((nb.data or {}).get("neighbors", []) if nb.ok else []):
                    rel = n.get("relationship")
                    if rel_types and rel not in rel_types:
                        continue
                    tnode = n.get("node") or {}
                    tid = tnode.get("id")
                    if not tid:
                        continue
                    a, b = (nid, tid) if n.get("direction") == "out" else (tid, nid)
                    key = (a, rel, b)
                    if key not in edge_seen:
                        edge_seen.add(key)
                        edges.append({"source": a, "relationship": rel, "target": b,
                                      "confidence": (tnode.get("properties") or {}).get("confidence")})
                    if tid not in seen:
                        nxt.append(tid)
            if capped or len(seen) >= max_nodes:
                capped = True
                break
            frontier = nxt
        # Materialize edge endpoints as stub nodes only up to the cap, then keep only edges whose
        # BOTH endpoints are materialized — the view never exceeds max_nodes or dangles an edge.
        for e in edges:
            for endp in (e["source"], e["target"]):
                if endp not in seen:
                    if len(seen) >= max_nodes:
                        capped = True
                        continue
                    seen[endp] = {"id": endp, "type": "unknown", "label": endp}
        kept = [e for e in edges if e["source"] in seen and e["target"] in seen]
        return {"root": root, "depth": depth, "max_nodes": max_nodes,
                "capped": capped or len(seen) >= max_nodes,
                "node_count": len(seen), "edge_count": len(kept),
                "nodes": list(seen.values()), "edges": kept}

    # ---- system health --------------------------------------------------------------------------
    async def system_health(self) -> dict[str, Any]:
        from datetime import UTC, datetime
        checked_at = datetime.now(UTC).isoformat()
        services = {}
        for name in self.gw._bases:
            h = await self.gw.get(name, "/health")
            health = h.data if h.ok else None
            services[name] = {
                "available": h.available, "status": h.status, "health": health,
                "version": self._service_version(health),
                "flags": {k: v for k, v in (health or {}).items() if k.endswith("_enabled")},
                "last_check": checked_at,
            }
        # data quality from crawl coverage + entity coverage
        cov = await self.gw.get(CRAWL, "/v1/internal/capture-schedule", params={"limit": 500})
        ent = await self.gw.get(CRAWL, "/v1/internal/entity-resolution/coverage")
        jobs = await self.gw.get(CRAWL, "/v1/internal/capture-schedule/jobs", params={"limit": 200})
        tracked = (cov.data or {}).get("events", []) if cov.ok else []
        et = (ent.data or {}).get("by_entity_type", {}) if ent.ok else {}
        job_list = (jobs.data or {}).get("jobs", []) if jobs.ok else []
        entities = await self.gw.get(CRAWL, "/v1/internal/entity-resolution/entities",
                                     params={"limit": 200})
        elist = (entities.data or {}).get("entities", []) if entities.ok else []

        # bounded per-event hydration for date/venue completeness
        missing_dates = missing_venue = 0
        for t in tracked[: self.EVENT_HYDRATE_CAP]:
            cid = t.get("canonical_event_id")
            if not cid:
                continue
            node = await self.gw.get(GRAPH, f"/v1/graph/nodes/{cid}")
            props = (node.data or {}).get("properties", {}) if node.ok else {}
            if not props.get("starts_at"):
                missing_dates += 1
            if not (props.get("venue") or props.get("venue_name")):
                missing_venue += 1

        data_quality = {
            "events_missing_dates": missing_dates,
            "events_missing_venue": missing_venue,
            "events_missing_geography": sum(1 for t in tracked if not t.get("city")),
            "ambiguous_artists": et.get("ARTIST", {}).get("ambiguous_mentions", 0),
            "ambiguous_venues": et.get("VENUE", {}).get("ambiguous_mentions", 0),
            "weak_series_detections": et.get("EVENT_SERIES", {}).get("ambiguous_mentions", 0),
            "legacy_canonical_duplicates": sum(1 for e in elist
                                               if e.get("identity_state") == "POSSIBLE_DUPLICATE"),
            "orphan_source_handles": sum(1 for e in elist
                                         if (e.get("source_handles") or 0) > 0
                                         and (e.get("linked_event_count") or 0) == 0),
            "stale_tracked_events": sum(1 for t in tracked
                                        if (t.get("capture_gap_hours") or 0) >= STALE_GAP_HOURS),
            "failed_capture_jobs": sum(1 for j in job_list
                                       if str(j.get("status", "")).startswith("FAILED")),
        }
        # gateway migration status (Admin Phase B) + governed canonical counts
        from api_gateway.db.session import migration_status
        counts = await self.governance_counts()
        return {"services": services, "data_quality": data_quality, "checked_at": checked_at,
                "gateway_migration": migration_status(),
                "feature_flags": {
                    "admin_api_enabled": settings.admin_api_enabled,
                    "admin_local_mode": settings.admin_local_mode,
                    "admin_operational_actions_enabled": settings.admin_operational_actions_enabled,
                },
                "canonical_counts": {
                    "raw_graph_entities_from_candidates": sum(
                        v.get("unique_canonical_entities", 0) for v in et.values()),
                    "canonical_resolved_entities": counts.get("canonical_resolved_entities"),
                    "legacy_superseded_nodes": counts.get("legacy_superseded_nodes")}}

    @staticmethod
    def _service_version(health: dict | None) -> str | None:
        if not health:
            return None
        for key in ("commit", "version", "git_sha", "build"):
            if health.get(key):
                return str(health[key])
        return None

    # ---- search ---------------------------------------------------------------------------------
    async def search(self, q: str, *, limit: int = 20) -> dict[str, Any]:
        ql = q.strip().lower()
        results: dict[str, list] = {"events": [], "entities": []}
        if not ql:
            return {"query": q, "results": results}
        ents = await self.gw.get(CRAWL, "/v1/internal/entity-resolution/entities", params={"limit": 200})
        for e in ((ents.data or {}).get("entities", []) if ents.ok else []):
            if ql in (e.get("canonical_name") or "").lower() or ql in e.get("canonical_entity_id", "").lower():
                results["entities"].append({"canonical_entity_id": e["canonical_entity_id"],
                                            "name": e.get("canonical_name"), "type": e["entity_type"],
                                            "identity_state": e.get("identity_state")})
        cov = await self.gw.get(CRAWL, "/v1/internal/capture-schedule", params={"limit": 500})
        for t in ((cov.data or {}).get("events", []) if cov.ok else []):
            cid = (t.get("canonical_event_id") or "")
            if ql in cid.lower() or ql in (t.get("source_record_id") or "").lower():
                results["events"].append({"canonical_event_id": cid, "source": t.get("source"),
                                          "source_record_id": t.get("source_record_id")})
        results["events"] = results["events"][:limit]
        results["entities"] = results["entities"][:limit]
        return {"query": q, "results": results}

    # ---- operational actions (OPERATOR; audited by the route) ------------------------------------
    async def rerun_enrichment(self, event_id: str) -> dict[str, Any]:
        r = await self.gw.post(CRAWL, f"/v1/internal/events/{event_id}/enrichment/resolve")
        return {"ok": r.ok, "status": r.status, "result": r.data}

    async def rerun_entity_resolution(self, event_id: str, source: str, source_record_id: str) -> dict[str, Any]:
        r = await self.gw.post(CRAWL, "/v1/internal/entity-resolution/resolve",
                               params={"event_id": event_id, "source": source,
                                       "source_record_id": source_record_id})
        return {"ok": r.ok, "status": r.status, "result": r.data}

    async def capture_now(self, source: str, source_record_id: str,
                          canonical_event_id: str | None = None) -> dict[str, Any]:
        r = await self.gw.post(CRAWL, "/v1/internal/capture-schedule/capture-now",
                               params={"source": source, "source_record_id": source_record_id,
                                       **({"canonical_event_id": canonical_event_id} if canonical_event_id else {})})
        return {"ok": r.ok, "status": r.status, "result": r.data}

    # ---- governance commands (proxied to crawl's owned command surface) -------------------------
    async def governance(self, path: str, payload: dict) -> Down:
        return await self.gw.post(CRAWL, f"/v1/internal/governance/{path}", json=payload)

    async def governance_counts(self) -> dict[str, Any]:
        r = await self.gw.get(CRAWL, "/v1/internal/governance/counts")
        return {"available": r.available, **(r.data or {})} if r.data else {"available": r.available}

    # ---- bounded export (Phase C) ---------------------------------------------------------------
    EXPORT_MAX_ROWS = 5000
    EXPORTABLE = ("events", "entities", "resolution-queue", "capture-jobs", "source-diagnostics")

    async def export_rows(self, table: str, *, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """Return flat rows for a table honouring the same filters as the live view, bounded."""
        cap = self.EXPORT_MAX_ROWS
        if table == "events":
            data = await self.events(**{**filters, "limit": cap, "offset": 0})
            return data.get("events", [])
        if table == "entities":
            data = await self.entities(**{**filters, "limit": min(cap, 200), "offset": 0})
            return data.get("entities", [])
        if table == "resolution-queue":
            data = await self.resolution_queue(**{**filters, "limit": cap})
            return data.get("items", [])
        if table == "capture-jobs":
            data = await self.capture_jobs(**{**filters, "limit": min(cap, 200), "offset": 0})
            return data.get("jobs", [])
        if table == "source-diagnostics":
            src = filters.get("source")
            sources = [src] if src else ["boshow", "district"]
            rows = []
            for s in sources:
                d = await self.source_diagnostics(s)
                rows.append({k: v for k, v in d.items()
                             if not isinstance(v, (dict, list)) or k in ("failure_classifications",)})
            return rows
        raise ValueError(f"unknown export table {table!r}")
