# ADR 0015 — Shared ticketing adapter contract + Skillbox pilot (Phase 4C)

- Status: Accepted
- Date: 2026-08-05
- Phase: 4C — Shared Ticketing Adapter Framework and Skillbox Pilot
- Extends: [ADR-0009](0009-second-source-and-reconciliation.md), [ADR-0010](0010-cross-inventory-entity-resolution.md)
- Relates to: [docs/ticketing-adapters.md](../ticketing-adapters.md), [docs/skillbox-probe.md](../skillbox-probe.md),
  [docs/soundcharts-feasibility.md](../soundcharts-feasibility.md)

## Context

Ticketing acquisition had grown per-provider. Adding a third live source (Skillbox) risked more ad-hoc
divergence, and Skillbox's Phase 3 probe showed it is low-quality (placeholder venues, tz-naive dates,
far-future pre-sale shells). We need one typed contract every source implements, a deterministic quality
gate before enrollment, and a bounded Soundcharts feasibility study — **without** rewriting signal-service
or implementing Soundcharts.

## Decisions

1. **One typed adapter contract, additive.** `TicketingAdapter` (discover · fetch_event · normalize_event ·
   classify_failure · extract_source_handles · extract_asset_references). `BaseTicketingAdapter` wraps the
   existing `TicketingProvider` factory + module-level normalization, so Boshow/District/Skillbox conform
   with **no behavioural regression** (source ids, graph/Shadow-Ledger writes, image extraction, failure
   semantics preserved; contract-compliance tests assert it). Downstream services consume only the
   normalized `TicketingEvent`.

2. **Deterministic quality gate before enrollment.** `validate_ticketing_event` rejects placeholder /
   malformed / non-event records (twelve reason codes) with precision over recall. Geography maps internal
   ids only through an explicit verified-city map (never a numeric id as a city; rejects Multiple/Mutiple
   Cities), keeps direct-source geography separate from derived, and never guesses coordinates. Dates are
   normalized to tz-aware from the source tz, else the verified-city tz only; naive+unverified stays naive
   with a warning; far-future placeholders are flagged; undated pages never become scheduled events. No
   graph events are created for rejected records; rejection counts + sampled reasons are kept for
   diagnostics. Field status is reported as present / valid / specific (resolved is a capture-time metric).

3. **Validated discovery + per-source diagnostics.** `validated_discovery` partitions candidates into
   accepted / rejected / out-of-scope; internal `/v1/internal/sources/{source}/…` endpoints report quality,
   rejections, coverage and a sample (bounded, no raw HTML). Always **observed supply**, never total-market
   coverage. Capture-time metrics stay with the pipeline (crawl + admin BFF).

4. **Pipeline parity by configuration, not a new pipeline.** Skillbox runs the identical
   discovery → validation → tracked_event → capture → Shadow Ledger → enrichment → entity resolution →
   media path, enabled by adding `skillbox` to the crawl source-set env lists. Entity/media hooks remain
   best-effort; a hook failure never fails capture; retry/locking/idempotency unchanged; Boshow/District
   unaffected. All Skillbox behaviour is flag-gated, defaults off.

5. **Convergence invariant preserved.** Skillbox contributes source-handle evidence for
   artists/venues/organizers/series/regions; a **shared canonical entity never implies a duplicate event**,
   and no event is auto-matched merely for sharing an artist/venue/organizer. Real overlap is reported
   honestly (zero when the cohorts are disjoint); no threshold recalibration without labelled real
   examples.

6. **Soundcharts is a separate artist-intelligence provider, not a ticketing source.** A bounded
   feasibility study documents expected capabilities, what needs live-key verification (endpoint shapes,
   India-local audience granularity, quotas, pricing, licensing), a ~90-call proof-of-value set, and a
   caching policy — with **no** production integration and **no** fabricated endpoint availability.
   Soundcharts would sit behind a future provider-neutral `ArtistIntelligenceProvider`, never
   `TicketingAdapter`, keeping ticketing-supply observation separate from licensed artist intelligence.

## Live evidence (2026-08-05, docker, real data)

`/v1/internal/sources` lists boshow/district/skillbox with the six capabilities. A live bounded Kolkata
pass over Skillbox (25 records) accepted 0 Kolkata, rejected 1 `PLACEHOLDER_DATE` (real pre-sale shell
`taba-chake-india-tour-2026-…`), 24 out-of-scope; field quality showed titles 1.0/1.0/1.0, city
1.0/1.0/0.76, venue 1.0/0.8/0.8, date 1.0/0.96/**0.0** (all tz-naive) — confirming Skillbox's low quality
and the gate working. Boshow validated 5/5 accepted (city+venue specific 1.0), unaffected. No Kolkata
Skillbox inventory met validation in the bounded sample → no live cross-source convergence claimed.

## Consequences

- One contract makes a fourth source a small, well-scoped addition; quality is measured consistently.
- Skillbox is integrated and gated, but its live Kolkata value is currently low (sitemap not Kolkata-first,
  tz-naive dates, placeholder shells) — honestly reported; a paged Kolkata discovery is a bounded follow-up.
- Artist intelligence (Soundcharts) is scoped as a separate, licensed, provider-neutral layer for later.
