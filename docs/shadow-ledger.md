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
