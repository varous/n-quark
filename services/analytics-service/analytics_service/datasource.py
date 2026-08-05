"""Datasource loader (Phase 4A).

Builds the normalized in-memory `Dataset` the read models aggregate over, from the existing internal
APIs: crawl-service (capture coverage, canonical entities, resolved entities, governance) and
graph-service (event node properties, IN_REGION, Shadow Ledger). Read-only and bounded.

The supersession map is read from the graph's `SUPERSEDED_BY` edges (Phase B) for the legacy ids crawl
reports as superseded — so the canonicalizer folds them non-destructively. Nothing here mutates state.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from analytics_service.config import settings
from analytics_service.crawl_client import CrawlServiceClient
from analytics_service.graph_client import GraphServiceClient
from analytics_service.projection import Canonicalizer
from analytics_service.readmodels import Dataset, EntityMeta, ObservedEvent

_TYPE_BY_PREFIX = {"artist": "ARTIST", "venue": "VENUE", "organizer": "ORGANIZER",
                   "series": "EVENT_SERIES", "region": "REGION"}


class AnalyticsDataSource:
    def __init__(self, crawl: CrawlServiceClient | None = None,
                 graph: GraphServiceClient | None = None) -> None:
        self.crawl = crawl or CrawlServiceClient()
        self.graph = graph or GraphServiceClient()

    async def load(self, *, now: datetime | None = None) -> Dataset:
        now = now or datetime.now(UTC)
        warnings: list[str] = []
        async with httpx.AsyncClient(timeout=15.0) as hc:
            tracked = await self.crawl.capture_schedule(hc, limit=settings.analytics_max_events)
            entity_rows = await self.crawl.entities(hc, limit=200)
            gov = await self.crawl.governance_counts(hc)

            if len(tracked) >= settings.analytics_max_events:
                warnings.append(f"tracked-event load hit the {settings.analytics_max_events} cap; results bounded")

            # --- entity metadata + identity states ---
            entities: dict[str, EntityMeta] = {}
            for r in entity_rows:
                cid = r.get("canonical_entity_id")
                if not cid:
                    continue
                etype = r.get("entity_type") or _TYPE_BY_PREFIX.get(cid.split(":", 1)[0], "UNKNOWN")
                entities[cid] = EntityMeta(
                    canonical_entity_id=cid, entity_type=etype,
                    canonical_name=r.get("canonical_name"),
                    identity_state=r.get("identity_state", "UNKNOWN"),
                    sources=list(r.get("sources") or []),
                    # upstream (Phase B) safeguard prevents weak/year-only series creation → strong
                    strong_series_marker=(etype == "EVENT_SERIES"),
                )

            # --- supersession map from graph SUPERSEDED_BY edges (legacy -> canonical) ---
            superseded_ids = list(gov.get("superseded_legacy_ids") or [])
            supersession = await self._build_supersession(superseded_ids)
            for legacy in supersession:
                entities.setdefault(legacy, EntityMeta(
                    canonical_entity_id=legacy,
                    entity_type=_TYPE_BY_PREFIX.get(legacy.split(":", 1)[0], "UNKNOWN"),
                    identity_state="SUPERSEDED", superseded=True))
                entities[legacy].superseded = True
                entities[legacy].identity_state = "SUPERSEDED"

            identity_states = {cid: m.identity_state for cid, m in entities.items()}
            canon = Canonicalizer(supersession=supersession, identity_states=identity_states,
                                  known_ids=set(entities.keys()))

            # --- per-event hydration (bounded fan-out) ---
            sem = asyncio.Semaphore(8)

            async def hydrate(t: dict) -> ObservedEvent | None:
                cid = t.get("canonical_event_id")
                if not cid:
                    return None
                async with sem:
                    node = await self.graph.get_node(cid)
                    neigh = await self.graph.neighbors(cid, direction="out")
                    resolved = await self.crawl.resolved_entities(hc, cid)
                    ledger = await self.graph.shadow_ledger(cid)
                return self._build_event(t, node, neigh, resolved, ledger)

            events = [e for e in await asyncio.gather(*(hydrate(t) for t in tracked)) if e is not None]

        # derive venue city/region from the events they appear in (source geography only)
        self._enrich_venue_geography(events, entities, canon)

        sources = sorted({e.source for e in events})
        return Dataset(events=events, entities=entities, canonicalizer=canon,
                       sources=sources, now=now, warnings=warnings)

    async def _build_supersession(self, legacy_ids: list[str]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for legacy in legacy_ids:
            neigh = await self.graph.neighbors(legacy, direction="out", relationship="SUPERSEDED_BY")
            for n in neigh:
                target = (n.get("node") or {}).get("id")
                if target:
                    mapping[legacy] = target
                    break
        return mapping

    @staticmethod
    def _build_event(t: dict, node: dict | None, neigh: list[dict],
                     resolved: list[dict], ledger: dict | None) -> ObservedEvent:
        props = (node or {}).get("properties", {}) if node else {}
        region = None
        for n in neigh or []:
            if n.get("relationship") == "IN_REGION":
                region = (n.get("node") or {}).get("id")
                break

        by_type: dict[str, list[str]] = {"ARTIST": [], "VENUE": [], "ORGANIZER": [], "EVENT_SERIES": []}
        unresolved = False
        for r in resolved:
            etype = (r.get("entity_type") or "").upper()
            cid = r.get("canonical_entity_id")
            status = (r.get("status") or "").upper()
            if not cid or status not in ("RESOLVED", "POSSIBLE_MATCH", ""):
                unresolved = True
            if cid and etype in by_type:
                by_type[etype].append(cid)

        transition_types: dict[str, int] = {}
        out_of_order = 0
        if ledger:
            for tr in ledger.get("transitions", []):
                tt = tr.get("transition_type")
                if tt:
                    transition_types[tt] = transition_types.get(tt, 0) + 1
                if tr.get("out_of_order"):
                    out_of_order += 1

        return ObservedEvent(
            canonical_event_id=t.get("canonical_event_id"),
            source=t.get("source"),
            source_record_id=t.get("source_record_id"),
            city=props.get("city") or t.get("city"),
            region=region,
            category=props.get("category"),
            starts_at=props.get("starts_at"),
            price_min=props.get("price_min"),
            price_max=props.get("price_max"),
            currency=props.get("currency"),
            fill_ratio=props.get("fill_ratio"),
            tickets_sold=props.get("tickets_sold"),
            capacity=props.get("capacity"),
            capture_count=t.get("capture_count", 0) or 0,
            distinct_state_count=t.get("distinct_state_count", 0) or 0,
            transition_count=t.get("transition_count", 0) or 0,
            capture_gap_hours=t.get("capture_gap_hours"),
            last_capture_status=t.get("last_capture_status"),
            consecutive_failures=t.get("consecutive_failures", 0) or 0,
            consecutive_absences=t.get("consecutive_absences", 0) or 0,
            out_of_order_count=out_of_order,
            artists=by_type["ARTIST"], venues=by_type["VENUE"],
            organizers=by_type["ORGANIZER"], series=by_type["EVENT_SERIES"],
            has_unresolved_entities=unresolved,
            transition_types=transition_types,
        )

    @staticmethod
    def _enrich_venue_geography(events: list[ObservedEvent], entities: dict[str, EntityMeta],
                                canon: Canonicalizer) -> None:
        for ev in events:
            for vid in ev.venues:
                cid = canon.canonical_id(vid)
                meta = entities.get(cid)
                if meta and meta.city is None and ev.city:
                    meta.city = ev.city
                if meta and meta.region is None and ev.region:
                    meta.region = ev.region
