# ADR 0010 — Cross-inventory entity resolution (Phase 3.1)

- Status: Accepted
- Date: 2026-08-04
- Phase: Phase 3.1 — Cross-Inventory Entity Resolution
- Extends: [ADR-0007](0007-capture-enrichment.md), [ADR-0008](0008-source-family-and-pilot.md), [ADR-0009](0009-second-source-and-reconciliation.md)
- Relates to: [docs/entity-resolution.md](../entity-resolution.md), [docs/ontology.md](../ontology.md)

## Context

Ticketing platforms mostly carry **exclusive** inventory — the same event rarely appears on both
Boshow and District (Phase 3 confirmed ~0 live duplicate-event overlap: grassroots Kolkata vs
mainstream nationwide). Duplicate-event reconciliation (Phase 3) therefore converges almost nothing on
its own. The valuable convergence layer is *different events → the same artist / venue / organizer /
series / region*. That makes two platform-exclusive catalogues comparable without pretending their
events are the same.

## Decisions

1. **Resolve entities, not just events.** A new deterministic layer extracts artist / venue /
   organizer / event-series evidence from every captured source event and resolves each onto a shared
   canonical entity. Platform-exclusive events stay distinct; only the *entities they reference*
   converge. This runs in crawl-service (own tables + best-effort scheduler hook), reusing the graph as
   the canonical entity store and the existing `{type}:{slug}` id convention — **not** a parallel
   entity store.

2. **Source handles are the durable identity, names are only matching evidence.** Each mention yields a
   per-source handle (`{source}:{type}:{slug}`) recorded in `entity_source_handle` → canonical. A handle
   is strong future-resolution evidence; a normalized name alone is never treated as a permanent global
   id. Cross-source convergence = two source handles resolving to one canonical id.

3. **Conservative, ambiguity-aware, per-type resolvers.** Pure functions per entity type with explicit
   reason codes. They **refuse to auto-resolve** an ambiguous artist name, a generic venue name without
   geography, or a generic series title without an organizer; a **tribute/cover act never collapses**
   into the original (the marker stays in its identity); **same venue name in a different city stays a
   distinct location** (venue ids are city-scoped `venue:{name}--{city}`); **same series title under a
   different organizer does not link**. Below-threshold RESOLVED decisions are downgraded to
   POSSIBLE_MATCH, so the per-type auto-resolve thresholds are the real gate.

4. **Linkage without event collapse.** Resolved entities are written as graph relationships —
   `source_handle -IDENTIFIES-> canonical`, and `event -FEATURES/OCCURS_AT/ORGANIZED_BY/PART_OF_SERIES->
   canonical`. Shared entities may *improve* Phase 3 event-match signals but **never auto-create an
   event match**: two events with the same artist, or two editions of a series, remain different events.

5. **Auditable, additive, default-off.** Every decision is stored with status / score / reason /
   supporting+contradicting signals; status transitions (e.g. a venue UNRESOLVED → RESOLVED once a city
   arrives) are appended to `entity_resolution_history` — source evidence is never rewritten. Migration
   `005` is additive/reversible; `ENTITY_RESOLUTION_ENABLED` defaults off; capture is unchanged when
   disabled and never fails when resolution fails.

## Live evidence (2026-08-04, full docker stack, real Boshow + District cohorts)

- 19 captured events resolved: 17 SUCCEEDED, 2 PARTIAL. Coverage — ARTIST 24/26 resolved (2 ambiguous:
  `Pilu`, `BWS` — short single-token names correctly queued, not merged); VENUE 19/19; ORGANIZER 8/8;
  SERIES 2/2. Graph gained IDENTIFIES (48), ORGANIZED_BY (8), PART_OF_SERIES (2) edges.
- `Skinny Mos` (a Kolkata venue that also appears as a performer) resolved to `venue:skinny-mos--kolkata`
  and `artist:skinny-mos`; repeat events converged via SOURCE_HANDLE_MATCH — the handle registry works.
- `THE ABOMINATION XII` → `series:the-abomination`, edition 12 preserved as a distinct event.
- **Live cross-source entity overlap is 0** in the current cohort (disjoint catalogues) — reported
  honestly. The cross-source convergence *mechanics* were proven with a clearly-labeled fixture pair
  (a Kolkata Boshow event and a Mumbai District event both featuring "Prateek Kuhad") injected into the
  live stack: both converged onto one `artist:prateek-kuhad` with two source handles, while a
  reconciliation run over the same two events produced **0 event matches** (different cities) — shared
  entity ≠ duplicate event. The fixture rows were removed afterwards.

## Consequences

- Two platform-exclusive inventories become comparable through shared canonical artists/venues/
  organizers/series and regional analytics, without any fabricated event overlap.
- Deterministic year-marker series detection has known false positives (e.g. "F1 2026 …" reads "2026"
  as an edition); harmless (a distinct series node, event preserved), documented as a limitation.
- Short but legitimate single-token artist names (e.g. "Pilu") are conservatively queued as AMBIGUOUS
  rather than risk a wrong merge — precision over recall, by design.
