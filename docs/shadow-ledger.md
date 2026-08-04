# Shadow Ledger — Phase 1 (implemented) vs roadmap (future)

This documents what the **Minimum Viable Shadow Ledger** actually ships today, and draws a hard line
between that and the broader strategy in
[product-spec.md → Independent Market Observation and Temporal Data Moat](product-spec.md#independent-market-observation-and-temporal-data-moat).
Decisions are recorded as ADRs in [`docs/adr/`](adr/).

## What is implemented now `[CURRENT]`

A narrow, auditable, reversible slice: repeatedly observe a public commercial event state, preserve
every distinct version, detect deterministic transitions, link them to the canonical event, and
expose an internal, traceable history — without touching the public feed or existing pipeline.

- **Storage** (graph-service, Postgres, additive migration `002`): `shadow_state` (append-only
  normalized commercial states) + `shadow_transition` (immutable transitions). Relational, keyed by
  the canonical `event:<slug>` id — the `GraphStore` abstraction is untouched (ADR-0002).
- **Detector** ([`graph_service/shadow_ledger.py`](../services/graph-service/graph_service/shadow_ledger.py)):
  pure, deterministic, no LLM. Normalizes fields, computes a volatile-free `state_hash`, and diffs
  against the previous state. Non-monotonic-safe (a decrease is a value change, never inferred as a
  refund). Null is distinct from zero; numbers/timestamps are canonicalized.
- **Write path** ([`shadow_store.py`](../services/graph-service/graph_service/shadow_store.py)):
  `observe()` suppresses no-op re-captures by hash (idempotent), appends genuinely-distinct states,
  records de-duplicated transitions.
- **Transition vocabulary** (Phase 1 only): `EVENT_FIRST_SEEN`, `PUBLIC_PRICE_CHANGED`,
  `PUBLIC_CAPACITY_CHANGED`, `PUBLIC_TICKETS_SOLD_CHANGED`, `PUBLIC_FILL_RATIO_CHANGED`,
  `PUBLIC_AVAILABILITY_CHANGED`, `EVENT_DATE_CHANGED`, `VENUE_CHANGED`, `EVENT_STATUS_CHANGED`,
  `EVENT_DISAPPEARED`, `EVENT_REAPPEARED`.
- **Internal API** (NOT public; ADR-0002):
  - `POST /v1/internal/events/{event_id}/shadow-ledger/observe?trace=true`
  - `GET  /v1/internal/events/{event_id}/shadow-ledger?trace=true`
  `?trace=true` returns the evidence chain: source ref → observation → canonical event → normalized
  state → previous-state lookup → comparison → emitted transition.
- **Ingest wiring** (signal-service, ticketing): after resolve + graph projection, the event's public
  commercial state is recorded to the ledger — **best-effort and OFF by default** so ingest behaviour
  is unchanged unless enabled. `fill_ratio` is tagged `epistemic_status = observed_public_state`
  (ADR-0003) — never verified sell-through.
- **Disappearance** (ADR-0004): requires *authoritative* absence and a configurable count of
  consecutive misses (`NQUARK_SHADOW_LEDGER_DISAPPEARANCE_THRESHOLD`, default 2). A single failed
  crawl never disappears an event.
- **Feature flags:** `NQUARK_SHADOW_LEDGER_ENABLED` (signal-service default `false`, graph-service
  default `true`), `NQUARK_SHADOW_LEDGER_SOURCES`, `NQUARK_SHADOW_LEDGER_DISAPPEARANCE_THRESHOLD`.
  Disabled → current pipeline behaviour is byte-identical; the public `/v1/events` feed is unchanged.

Repeated capture in Phase 1 uses the **existing** ingest / cron / fixture-replay mechanisms — no
autonomous crawler was built (ADR-0004). The detector is callable by a future crawl-service unchanged.

## Phase 1.1 — Capture Completeness & Transition Integrity `[CURRENT]`

Hardening that makes repeated capture trustworthy *before* automated scheduling. Full rationale in
[ADR-0005](adr/0005-capture-completeness-integrity.md). Additive migration `003`; the public feed and
analytics contracts are unchanged.

- **Snapshot completeness**: every capture is `COMPLETE` or `PARTIAL`; callers that don't declare it
  default to **`PARTIAL`** (conservative — unknown callers are never treated as complete).
- **Field observation status** (per field): `OBSERVED_VALUE | OBSERVED_NULL | NOT_OBSERVED |
  EXTRACTION_FAILED | NOT_SUPPORTED`. Only `OBSERVED_VALUE`/`OBSERVED_NULL` can emit a transition.
  Unset statuses are inferred; a `None` value is inferred `NOT_OBSERVED`, **never** `OBSERVED_NULL`.
- **Effective-state merge**: the new effective state is the previous one with only validly-observed
  fields overlaid. Unobserved/failed/unsupported fields are **carried forward, never nulled** — so an
  omitted `starts_at` in a partial capture no longer fabricates `EVENT_DATE_CHANGED`.
- **Explicit null**: a value→null transition requires `OBSERVED_NULL` on a field whose registry entry
  permits it (Phase 1.1: `availability`, `status`). Otherwise it is suppressed
  (`EXPLICIT_NULL_NOT_ALLOWED`) and the previous value is kept.
- **Two hashes**: `capture_hash` (what this capture observed) vs `effective_state_hash` (the merged
  result, stored under the existing `state_hash` column). No-op idempotency is on the effective hash.
- **Out-of-order** (conservative): a capture older than the current latest — or an equal-timestamp
  capture with a conflicting payload — is persisted flagged `out_of_order=true` for audit and excluded
  from current-state / forward-transition emission. Equal timestamp + identical payload is idempotent.
  Timeline recomputation is deferred.
- **Disappearance is capture-status-driven**: `capture_status` distinguishes present / authoritative
  absence / failures / explicit removal. Only authoritative absence counts toward the threshold;
  failures never increment and are not persisted as states. `EVENT_DISAPPEARED` fires once at the
  threshold (or immediately on `EXPLICITLY_REMOVED`); a present capture resets the counter and emits a
  single `EVENT_REAPPEARED` if the event had disappeared.
- **Trace + observability**: `observe` returns `suppressed` transitions with reasons
  (`FIELD_NOT_OBSERVED`, `EXTRACTION_FAILED`, `EXPLICIT_NULL_NOT_ALLOWED`, `OUT_OF_ORDER`,
  `NO_VALUE_CHANGE`, `DUPLICATE_STATE`, `CONFLICTING_TIMESTAMP`, `DISAPPEARANCE_THRESHOLD_NOT_MET`);
  `?trace=true` shows completeness, field statuses, carried-forward fields, both hashes, and the
  emitted transitions.
- **Adapter contract**: `commercial_state()` returns a structured capture (`values`, `field_status`,
  `snapshot_completeness`, `capture_status`). Boshow marks a full show fetch `COMPLETE`, unexposed
  fields (`availability`, `status`) `NOT_SUPPORTED`, and `None`-valued supported fields
  `NOT_OBSERVED` — a model default of `None` is never asserted as an observed removal.

### Known limitations (Phase 1.1)

- Out-of-order captures are audited but do not trigger timeline reconciliation (no recomputation of
  intermediate effective states / transitions). Deferred.
- A `COMPLETE` capture that omits a field is treated conservatively as `NOT_OBSERVED` for that field
  (carried forward), so a genuine silent removal on a source that doesn't emit an explicit null may be
  missed — chosen deliberately to avoid false nulls.
- Non-authoritative failure captures are logged/returned but not persisted as state rows.

## Phase 2 — Controlled Scheduled Capture `[CURRENT]`

Repeated Boshow captures run automatically so histories accumulate — the smallest production-safe
scheduler around the *existing* ingest path. Full rationale in
[ADR-0006](adr/0006-scheduled-capture.md). Lives in **crawl-service**; additive migration `001`
(`alembic_version_crawl`); default **off**.

**Architecture (service boundaries only):**
`crawl-service scheduler → (HTTP) signal-service ingest → (HTTP) graph-service Shadow Ledger`.
The scheduler never touches the detector; present captures go through signal-service (which submits
the structured capture to the Shadow Ledger), and authoritative absence is posted to the Shadow
Ledger `observe` endpoint.

- **Lifecycle:** `sync` (discover + enroll Boshow events) → each run: recover expired locks →
  generate due jobs → claim (lease lock) → capture via signal-service → classify → update coverage →
  schedule next capture.
- **Tables:** `tracked_event` (operational coverage) + `scheduled_capture_job` (lease-locked,
  idempotent). Job identity = `source:source_record_id:capture_window` (unique) → duplicate cron /
  concurrent generation is a no-op.
- **Cadence** (deterministic, configurable): far-future/not-on-sale 24h · 15–30d 12h · final 14d 4h ·
  on-sale first 48h 2h · event day 2h · post-event +1/+3/+7d then stop. Returns `(next_capture_at,
  cadence_reason)`; falls back to event-date cadence when on-sale timing is unknown.
- **Priority** (deterministic, explainable): urgency + on-sale burst + recent transition + priority
  city − failure penalty; exposes score, dominant reason, and components.
- **Locking:** compare-and-swap claim (`UPDATE ... WHERE status='PENDING'`) — only one worker wins;
  expired leases are recovered to `PENDING` each run. A crash mid-capture is safe: the lease expires,
  the job re-runs, and the Shadow Ledger no-ops unchanged state (no duplicate transitions).
- **Retries / classification:** `SUCCESS_RECORD_PRESENT | SUCCESS_RECORD_ABSENT | SOURCE_UNAVAILABLE |
  RATE_LIMITED | TIMEOUT | PARSER_FAILED | INVALID_RESPONSE | TERMINAL_EVENT`. Bounded exponential
  backoff (honours `Retry-After`); parser failures → limited retries → `NEEDS_REVIEW`. **A failed
  request is never recorded as absence** — absence requires a successful request that reports the
  record gone (signal-service `EventNotFound` → HTTP 404).
- **Operational coverage (internal API):**
  `GET /v1/internal/capture-schedule[/{source}/{source_record_id}]` → next/last capture, last
  success, capture status, consecutive failures/absences, capture/distinct-state/transition counts,
  capture gap, cadence + priority reasons, lock status. `POST .../sync` and `POST .../run?trace=true`
  are gated by the enabled flag.
- **Worker:** `python -m crawl_service.worker` (run-to-completion; compatible with the Fly cron
  pattern). Idempotent — safe to over-invoke.
- **Feature flags:** `NQUARK_SCHEDULED_CAPTURE_ENABLED` (default `false`),
  `NQUARK_SCHEDULED_CAPTURE_SOURCES` (default `boshow`), `NQUARK_SCHEDULED_CAPTURE_MAX_JOBS`,
  `NQUARK_SCHEDULED_CAPTURE_LOCK_TTL_SECONDS`, `NQUARK_SCHEDULED_CAPTURE_CITY_ALLOWLIST`,
  `NQUARK_SCHEDULED_CAPTURE_MAX_TRACKED`. Disabled → crawl-service behaves as before; migrations run
  only when enabled.

### Known limitations (Phase 2)

- Boshow only. `starts_at`/`on_sale_at`/`city` are not back-filled from discovery, so newly enrolled
  events use conservative `no_event_date`/date-based cadence until those fields are populated.
- Out-of-order handling is Phase 1.1's conservative audit-only behaviour (no timeline reconciliation).
- No breadth-first crawling, multi-source reconciliation, coverage scoring, or commercial analytics.

## Phase 2.1 — Evidence-Based Capture Enrichment `[CURRENT]`

Enriches scheduled Boshow events with date/venue/city/region/on-sale timing so the Phase 2 cadence
actually engages. Lives in **crawl-service**; additive migration `002`; default **off**. Full
rationale in [ADR-0007](adr/0007-capture-enrichment.md).

- **Candidate vs resolution:** evidence produces `enrichment_candidate` rows (one per field-value per
  surface, full provenance: source_type, extraction_method, confidence, content_hash, observed_at);
  a deterministic resolver produces one *current* `event_field_resolution` per field (versioned —
  prior resolutions kept `is_current=False`). Weakly-inferred values are never written straight to
  canonical/scheduling records.
- **Two paths (this phase):** (1) **canonical graph relationship** — the event's graph node carries
  `starts_at`/`city` and `OCCURS_AT`→venue / `IN_REGION`→region, so venue/city/region are *derived*
  (`CANONICAL_RELATIONSHIP`), never asserted as a direct field; (2) **Boshow public-page structured
  metadata** — JSON-LD (title-matched, not "first Event"), embedded state, Open Graph, labelled
  visible text (flag-gated `NQUARK_CAPTURE_ENRICHMENT_PUBLIC_PAGE_ENABLED`).
- **Source precedence** (field-specific registry): direct structured field → JSON-LD → embedded state
  → Open Graph → visible text → canonical relationship → temporal; city/region/venue_id put canonical
  relationship first.
- **Deterministic resolver (no LLM):** higher authority wins; independent agreement raises confidence
  (`RESOLVED_CONSENSUS`); equal-authority disagreement → `CONFLICT` (unresolved, flagged); stale/lower
  never overrides newer/higher; below min-confidence → `NEEDS_REVIEW`; **no evidence → unresolved,
  never guessed**. A missing/unparseable field yields **no candidate** (never a null).
- **On-sale timing** is never an invented point: `source_on_sale_at` only from an explicit source
  timestamp; not-on-sale→on-sale gives an interval (`estimated_on_sale_window_start/_end`); a
  first-already-on-sale observation records only `first_ticket_state_seen_at`.
- **Scheduler integration:** `tracked_event` is updated **only** from auto-resolved fields; partial
  enrichment never erases values; conflicts never update scheduling; a date/status change recalculates
  `next_capture_at`; the city allow-list is re-checked. Enrichment is **best-effort** and never fails
  the capture.
- **Internal API:** `GET /v1/internal/events/{event_id}/enrichment` (resolved fields + candidate
  provenance) and `POST .../enrichment/resolve` (re-run for one event; flag-gated). `?trace=true`
  shows the pipeline (page requested → blocks discovered → candidates extracted/normalized → canonical
  venue resolution → resolver → conflicts → persisted → tracked_event updated → cadence recalculated),
  with suppression reasons (`FIELD_NOT_PRESENT`, `PARSER_FAILED`, `LOW_CONFIDENCE`, `AMBIGUOUS_VENUE`,
  `CONFLICTING_HIGH_AUTHORITY_VALUES`, `STALE_CANDIDATE`, `NO_CANONICAL_RELATIONSHIP`).
- **Flags:** `NQUARK_CAPTURE_ENRICHMENT_ENABLED` (default `false`), `..._SOURCES`,
  `..._PUBLIC_PAGE_ENABLED`, `..._MIN_CONFIDENCE`.

### Deferred enrichment sources `[FUTURE]`

Documented as future source types, **not built** in this phase: cross-platform event matching,
poster OCR / vision, official social channels, search-engine enrichment, press extraction, and a
manual-review UI.

### Known limitations (Phase 2.1)

- Boshow only. The canonical-graph path works live; the public-page path is Boshow-specific and
  fixture-tested (live Boshow HTML is not fetched unless the public-page flag is on).
- Venue geography derives city/region from the event's canonical graph relationships; ambiguous venue
  names with no canonical relationship remain unresolved (never invented).

## Phase 2.2 — Live Enrichment Validation & Incremental Source Value `[CURRENT]`

Measures whether the Boshow public-page surface adds new, reliable fields beyond `Boshow API +
canonical graph`, so continuous collection is justified by evidence — not assumption. Lives in
crawl-service; additive migration `003`; default **off**. Full rationale + live findings in
[ADR-0008](adr/0008-source-family-and-pilot.md).

- **Source vs surface vs source-family vs independence-group.** Every candidate records a `surface`,
  a `source_family`, and an `independence_group`. All Boshow-record-derived surfaces (API, share page,
  JSON-LD, embedded state, OG, visible text, and the canonical-graph projection) share **one**
  independence group (`boshow_origin`); only n-quark's own temporal observation is independent.
- **Same-family agreement ≠ consensus.** The resolver grants `RESOLVED_CONSENSUS` + full boost only
  across **≥2 independence groups**; multiple same-family surfaces get a modest bump and stay
  `RESOLVED_DIRECT`. Higher-authority live API evidence still beats page metadata; date comparison is
  by wall clock; a newer same-value reconfirmation of a mutable field is `FRESHNESS_GAIN` (tracked
  separately, never consensus).
- **Live pilot** (behind `CAPTURE_ENRICHMENT_PILOT_ENABLED` + `..._PUBLIC_PAGE_ENABLED`): deterministic
  cohort sampling (seed), rate-limited timed fetch, response classification
  (`SUCCESS_HTML`/`NOT_FOUND`/`RATE_LIMITED`/`TIMEOUT`/`INVALID_HTML`/`BLOCKED_OR_CHALLENGE`/…),
  event-page validation (title/slug/markers; an error or challenge page never yields candidates),
  per-field **incremental / duplicate / conflict / freshness** classification, and an auditable
  `enrichment_run`. Measurement-only — it never mutates tracked_event or resolutions.
- **Reports (internal):** `POST /v1/internal/enrichment/pilot/run?trace=true`,
  `GET …/pilot/runs`, `GET …/source-value` (field-level coverage, incremental-gain rate, duplicate /
  conflict / freshness, presence rates, latency/bytes), `GET …/venue-coverage` (how well *known* events
  are grounded geographically — not market coverage). Each run ends with an evidence-driven
  recommendation (`PROMOTE_TO_STANDARD_ENRICHMENT` / `KEEP_AS_FALLBACK` / `DISABLE_LOW_VALUE` /
  `REQUIRES_SOURCE_FIX`) with components + reasons.

### Live verdict (real Boshow, 2026-08-03)

`…/shows.html?slug=` → 404; `…/api/shows/share/{slug}` → 200 but exposes **only Open Graph** (title,
image, and `og:description = "Aug 01, 2026, 8:00 PM Skinny Mos"`) — **no JSON-LD, no embedded state**.
Measured over a 3-event cohort: retrieval 1.0, OG presence 1.0, JSON-LD 0.0; **INCREMENTAL 0,
DUPLICATE 2, FRESHNESS_GAIN 2, CONFLICT 0**. The page adds **no new fields** — only same-family OG
duplicates of date/venue at low authority. Recommendation: **not promoted** — `DISABLE_LOW_VALUE` at
the default confidence bar, `KEEP_AS_FALLBACK` (freshness only) at a relaxed bar. Kept behind flags.

### Known limitations (Phase 2.2)

- Boshow only; the share card is an OG-only social card, so incremental gain is structurally ~0.
- Freshness gain depends on capture recency vs the current resolution's age.
- The independence model is designed so a genuinely independent *second platform* (future) would
  produce real consensus — no Boshow surface can.

## Phase 3 — Independent Second Source (District) + Cross-Platform Reconciliation `[CURRENT]`

Adds one genuinely independent source (**District**, selected by a live probe — see
[ADR-0009](adr/0009-second-source-and-reconciliation.md)) to scheduled capture, then reconciles
overlapping Boshow/District listings into shared canonical events **without collapsing source truth**.
Lives in crawl-service; additive migration `004`; default **off**.

- **District capture parity:** signal-service's ticketing `discover`/`preview`/`ingest` accept a
  per-request `source`, so the Phase 2 scheduler captures Boshow and District through one route (no
  parallel scheduler). District gets its own source-specific Shadow Ledger history; `EventNotFound`
  makes absence authoritative (a failed request never becomes absence). Flags:
  `NQUARK_SECOND_SOURCE_CAPTURE_ENABLED`, `NQUARK_SECOND_SOURCE_NAME`.
- **Per-origin independence:** a candidate's `independence_group` comes from its originating platform
  (`boshow_origin` / `district_origin` / `nquark_temporal`), and graph projections inherit it. The
  resolver grants `RESOLVED_CONSENSUS` only across **different** origins — so Boshow + District
  agreement is real consensus, but any two Boshow surfaces are not.
- **Bounded blocking + deterministic matcher:** candidates are generated only within blocks (date
  tolerance, compatible city, shared title/performer/venue signal). The matcher scores
  title/performer/venue/city/date/organizer and **refuses auto-match under a strong contradiction**
  (different city, date beyond tolerance, non-overlapping performers) — title similarity never
  overrides. Outcomes: `MATCHED` (≥ auto threshold, ≥2 agreeing dims, compatible date, different
  sources) / `POSSIBLE_MATCH` / `CONFLICT` / `NOT_MATCHED`.
- **Linkage without truth collapse:** an accepted match links both source listings via
  `event_match_candidate` (`REPRESENTED_BY`); both source records, Shadow Ledger histories, enrichment
  candidates and displayed prices/availability are preserved — canonical ids are never merged.
- **Field reconciliation:** the resolver runs over both sources' candidates — independent agreement →
  consensus; one source fills a field the other lacks; conflicts stay explicit; source-specific
  price/availability are compared (`PLATFORM_DIFFERENCE` / `SAME` / `SINGLE_SOURCE`), not flattened.
- **Internal endpoints:** `POST /v1/internal/reconciliation/{probe,run}`,
  `GET /v1/internal/reconciliation/{matches,matches/{id},metrics}`,
  `GET /v1/internal/events/{id}/source-records`. Flags:
  `NQUARK_RECONCILIATION_ENABLED`, `..._AUTO_MATCH_THRESHOLD`, `..._POSSIBLE_MATCH_THRESHOLD`,
  `..._DATE_TOLERANCE_HOURS`, `..._MAX_PAIRS_PER_RUN`.

### Known limitations (Phase 3)

- District only (one independent source, as mandated). Live Boshow↔District overlap is ~0 (grassroots
  vs mainstream), so match/reconciliation mechanics are proven by fixture-backed tests, not a live
  match. Accepted matches are recorded but canonical-id merging is deliberately not performed.

## Phase 3.1 — Cross-Inventory Entity Resolution `[CURRENT]`

Platform-exclusive Boshow and District events are resolved onto a **shared canonical entity graph**
(artists / venues / organizers / event series) so exclusive catalogues become comparable through the
entities they share — without requiring the same event on both platforms and without collapsing distinct
events. Deterministic, additive, default-off (`ENTITY_RESOLUTION_ENABLED`). Full design +
endpoints: [entity-resolution.md](entity-resolution.md), [ADR-0010](adr/0010-cross-inventory-entity-resolution.md).

- Per-type deterministic resolvers with an explicit ambiguity policy: ambiguous names, generic venues
  without geography and generic series titles without an organizer do **not** auto-resolve; a tribute
  act never collapses into the original; same venue name in a different city stays distinct (city-scoped
  ids); same series title under a different organizer does not link.
- Source handles (`{source}:{type}:{slug}`) form the durable alias registry (`entity_source_handle`);
  cross-source convergence = two source handles → one canonical id. Decisions + status history are
  auditable (`entity_resolution_candidate`, `entity_resolution_history`).
- Graph gains `IDENTIFIES`, `ORGANIZED_BY`, `PART_OF_SERIES` edges. Shared entities **never** auto-create
  a duplicate-event match.

### Live verdict (real Boshow + District, 2026-08-04)

19 events resolved (17 SUCCEEDED, 2 PARTIAL); ARTIST 24/26, VENUE 19/19, ORGANIZER 8/8, SERIES 2/2;
graph +48 IDENTIFIES / +8 ORGANIZED_BY / +2 PART_OF_SERIES. `Skinny Mos` → `venue:skinny-mos--kolkata`
(+ handle convergence across repeat events); `THE ABOMINATION XII` → `series:the-abomination` (edition 12
preserved). **Live cross-source overlap = 0** (disjoint cohorts, reported honestly); convergence
mechanics proven with a labeled fixture pair (both platforms → one `artist:prateek-kuhad`, 0 event
matches). Fixture removed after.

### Known limitations (Phase 3.1)

- Convergence needs overlapping catalogues; current cohorts are disjoint. Year-marker series detection
  has harmless false positives ("F1 2026 …"). Short legitimate single-token names (e.g. "Pilu") are
  conservatively queued AMBIGUOUS. The ingest-time naive projection coexists with the corrected Phase 3.1
  canonical layer (unifying id conventions is a follow-up). No geocoding source yet.

## What is explicitly NOT in Phase 1 `[FUTURE]`

Deferred to the roadmap (see the MCP section + its backlog): prediction / ML sell-through, crowd
estimation, campaign-pressure analytics, crawl-space audience-intent instrumentation, contributor /
benchmark networks, federated computation, sales-curve classification, multi-source reconciliation,
source coverage ledger, adaptive scheduling, and any public/partner redistribution of transitions or
estimates. None of these are dependencies of Phase 1.

## Verification

- Unit + integration tests: [`test_shadow_ledger.py`](../services/graph-service/tests/test_shadow_ledger.py)
  (detector, hashing, idempotency, persistence, linkage, epistemic status, endpoints, `?trace=true`,
  and the A–F replay demonstration) + signal-service wiring tests.
- Live: tracer-validated end-to-end on Postgres — ingest → `EVENT_FIRST_SEEN` → idempotent no-op →
  value-change transitions → `?trace=true` evidence chain, with rows in `shadow_state` /
  `shadow_transition`.
