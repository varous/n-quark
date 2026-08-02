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
