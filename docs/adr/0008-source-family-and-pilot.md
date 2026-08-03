# ADR 0008 — Source-family semantics + Boshow public-page value verdict (Phase 2.2)

- Status: Accepted
- Date: 2026-08-03
- Phase: Phase 2.2 — Live Enrichment Validation and Incremental Source Value
- Extends: [ADR-0007](0007-capture-enrichment.md)
- Relates to: [docs/shadow-ledger.md](../shadow-ledger.md)

## Context

Phase 2.1 resolved fields from Boshow API + canonical-graph evidence and, optionally, the Boshow
public page. Before spending on continuous public-page collection we had to answer: **does the public
page add new, reliable fields, or only duplicate the API?** And we had to stop treating multiple
Boshow-derived surfaces as independent confirmation.

## Decisions

1. **Source vs surface vs source-family vs independence-group.** Every candidate carries a `surface`
   (api / public_json_ld / open_graph / visible_text / venue_relationship / temporal), a
   `source_family` (boshow / n_quark_graph / n_quark_observation), and an `independence_group`. All
   Boshow-record-derived surfaces — API, share page, JSON-LD, embedded state, OG, visible text, and
   the canonical-graph projection built from the Boshow ingest — share **one** independence group
   (`boshow_origin`). Only a genuinely different origin (n-quark's own temporal observation =
   `nquark_temporal`) is independent.
2. **Same-family agreement is not consensus.** The resolver grants `RESOLVED_CONSENSUS` + the full
   confidence boost only when supporting candidates span **≥2 independence groups**. Multiple
   same-family surfaces (e.g. API + its share page) get only a modest extraction bump and stay
   `RESOLVED_DIRECT`. Same-family conflict is still recorded; higher-authority live API evidence still
   beats lower-authority page metadata.
3. **Stale evidence cannot override newer authoritative evidence.** Date comparison is by wall clock;
   an older/lower-authority page candidate never overwrites newer API evidence; a materially newer
   same-value reconfirmation of a mutable field is `FRESHNESS_GAIN`, tracked separately from
   new-field gain (and never counted as consensus).
4. **Controlled live pilot.** Public-page retrieval is promoted from fixture-only to a live path
   behind `CAPTURE_ENRICHMENT_PILOT_ENABLED` + `CAPTURE_ENRICHMENT_PUBLIC_PAGE_ENABLED`: deterministic
   cohort sampling, rate-limited timed fetch, response classification, event-page validation (a
   generic error/challenge page never produces candidates), incremental-value measurement, and an
   auditable `enrichment_run`. Measurement-only — it does not mutate tracked_event or resolutions.
5. **Evidence-driven recommendation.** `recommend()` maps measured metrics (retrieval success,
   incremental gain rate, conflict rate, parser stability, freshness) to
   `PROMOTE_TO_STANDARD_ENRICHMENT | KEEP_AS_FALLBACK | DISABLE_LOW_VALUE | REQUIRES_SOURCE_FIX` via
   configurable thresholds, always exposing components + reasons. No favourable outcome is hard-coded.

## Live findings (real Boshow pages, 2026-08-03)

- `…/shows.html?slug=` → **404**; the working surface is `…/api/shows/share/{slug}` → **200**, a tiny
  card exposing **only Open Graph** (`og:title` = name, `og:image` = image, `og:description` =
  `"Aug 01, 2026, 8:00 PM Skinny Mos"`). **No JSON-LD, no embedded state.**
- Measured (3-event cohort): retrieval success **1.0**, OG presence **1.0**, JSON-LD presence
  **0.0**; fields evaluated 4 → **DUPLICATE 2, FRESHNESS_GAIN 2, INCREMENTAL 0, CONFLICT 0**; mean
  latency ~381 ms.
- **Verdict:** the public page yields **no new fields** beyond the API/graph — only same-family
  Open-Graph duplicates of `starts_at`/`venue_name`, at low authority. At the default confidence bar
  (0.6) these are low-confidence → `DISABLE_LOW_VALUE`; at a relaxed bar they register as
  duplicate/freshness → `KEEP_AS_FALLBACK`. Either way it does **not** justify promotion to continuous
  standard collection. Kept behind flags as an optional low-confidence freshness fallback.

## Consequences

- Additive migration `003` (candidate provenance columns + `enrichment_run`); public feed, analytics,
  Shadow Ledger, and Phase 2.1/2.2 scheduler behaviour unchanged when disabled.
- The independence model is reusable: a genuinely independent second platform (future) would produce
  real `RESOLVED_CONSENSUS`, unlike any Boshow surface.
