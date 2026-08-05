# ADR 0013 — Canonical analytics projection & market read models (Phase 4A)

- Status: Accepted
- Date: 2026-08-05
- Phase: 4A — Canonical Market Read Models
- Extends: [ADR-0010](0010-cross-inventory-entity-resolution.md), [ADR-0012](0012-governed-resolution-decisions.md)
- Relates to: [docs/analytics.md](../analytics.md), analytics-service `README.md`

## Context

n-quark has captured and normalized cross-platform data (Boshow, District) but produced no market-level
read models. The graph still holds **both** ingest-time naive-projection identities and evidence-resolved
canonical identities (e.g. `venue:skinny-mos` and `venue:skinny-mos--kolkata`; and Phase B added
`SUPERSEDED_BY` edges such as `venue:the-urban-theatre-project → venue:urban-theatre-project--kolkata`).
Aggregating naively would double-count. This phase adds deterministic market/entity/observation read
models that count by **canonical** id — without a destructive graph migration.

## Decisions

1. **Canonical query projection, not a migration.** A reusable read-layer canonicalizer folds a legacy
   or superseded id onto its canonical by following `SUPERSEDED_BY` then supported alias edges. It is
   **non-destructive** (reads relationship maps only), detects cycles / self-references / over-long
   (invalid) chains, warns on folds to unknown targets, and preserves the full `resolution_path`. Legacy
   nodes remain inspectable and queryable — a query for a legacy id returns its canonical view.

2. **Enumerate from the evidence-based canonical layer.** Read models enumerate entities from
   crawl-service's evidence-based canonical layer (which structurally excludes naive projections) and
   fold any residual legacy ids via the projection. Consequence: an unmerged legacy/canonical
   `POSSIBLE_DUPLICATE` that has **no** `SUPERSEDED_BY` edge is **not** silently merged — it is reported
   (and would be retired by a future governed supersession), never guessed. Precision over recall.

3. **Observed supply / observation quality — never market coverage.** Every response states its scope and
   limitations. We report what n-quark captured, not the total market. No prediction, demand, popularity
   or sell-through is computed (the legacy demand-scoring endpoints predate this phase and are retained
   only for compatibility, not extended).

4. **Commercial state from Shadow Ledger facts only.** Price/availability/status/date-or-venue changes and
   disappear/reappear are counted from observed transition types; price distributions are descriptive;
   **source-specific prices are kept separate**. Nothing is estimated.

5. **Strong series only.** Series read models include only strong canonical series (the Phase B
   year-only safeguard prevents weak series creation) and exclude superseded ones.

6. **Query-time read models first.** Aggregation is computed live from a bounded snapshot loaded per
   request from the existing internal APIs (crawl + graph). No new tables/migrations were added; the raw
   source and Shadow Ledger remain authoritative. Materialized tables are deferred until profiling shows
   live aggregation is too slow — and would then be additive, reversible, and deterministically
   refreshed. The current Boshow/District cohort (19 events) aggregates well within request latency.

7. **New surface is namespaced.** Market read models live under `/v1/analytics/market/...`, leaving the
   pre-existing `/v1/analytics/artists/{id}` and `/v1/analytics/regions/{id}` scoring endpoints (and
   their tests) untouched — compatibility preserved.

## Live evidence (2026-08-05, docker, real Boshow+District data)

`canonicalize/venue:the-urban-theatre-project → venue:urban-theatre-project--kolkata` (folded, counted
once; querying the legacy id returns the canonical view). `regions` → 7 region/city groups (west-bengal
11 boshow events / 23 artists / 8 venues; maharashtra 3 district events / 3 organizers). Artist *Skinny
Mos* → 4 events, 2 upcoming / 2 completed, venue folded to `venue:skinny-mos--kolkata`. Venue *Skinny
Mos* → 4 events, 4 with state transitions. Organizer *KICKASS ADVENTURES* → 2 events, multi-venue +
multi-city indicators. 2 strong series. Observation-quality → 19 tracked, 17 with 1+ transitions, avg
gap 26.07h. Commercial-state → 19 price observations, per-source price distributions kept separate
(boshow median ₹499, district median ₹549.5), disappear/reappear 3. `trace=true` shows the single
superseded fold and per-event inclusion.

## Consequences

- Market/entity/observation read models exist and are deterministic + explainable, counting by canonical
  id without a destructive migration.
- The naive-vs-canonical duplication can be retired one governed supersession at a time; analytics counts
  correctly in the meantime and reports (does not merge) the un-superseded duplicates.
- Region derivation depends on graph `IN_REGION` edges; events without a region fall back to a
  `city:{city}` group. Analytics uses graph-resolved geography (richer than the scheduler's tracked
  `city` column).
- Live aggregation fans out per event; a materialization layer is the documented next step if the cohort
  grows large.
