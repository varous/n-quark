# ADR 0006 — Controlled scheduled capture (Phase 2)

- Status: Accepted
- Date: 2026-08-03
- Phase: Phase 2 — Controlled Scheduled Capture
- Relates to: [ADR-0004](0004-scheduling-dependency.md), [ADR-0005](0005-capture-completeness-integrity.md), [docs/shadow-ledger.md](../shadow-ledger.md)

## Context

Phase 1.1 made captures trustworthy; the Shadow Ledger now needs *repeated* captures to accumulate
real histories. This is the smallest production-safe scheduling layer around the existing ingest
path — not the autonomous crawler.

## Decisions

1. **Home = crawl-service.** The scheduler lives in crawl-service (the crawl/scheduling domain) and
   drives capture over **HTTP service boundaries**: it calls signal-service's existing ticketing
   ingest route (which itself submits the structured capture to the Shadow Ledger), and posts
   authoritative absence to graph-service's Shadow Ledger *observe* endpoint. The scheduler never
   imports or calls the Shadow Ledger detector directly. signal-service's ingest path is unchanged.
2. **Two tables** (crawl-service, alembic `alembic_version_crawl`, migration `001`, additive):
   `tracked_event` (operational coverage per source event) and `scheduled_capture_job` (a lease-
   locked, idempotent unit of work).
3. **Idempotent job identity** = `dedup_key = source:source_record_id:capture_window`, where the
   window is the tracked event's `next_capture_at` (or `initial`). A unique index makes duplicate
   cron invocations and concurrent generation a no-op.
4. **Lease locking via compare-and-swap.** A claim is an atomic `UPDATE ... WHERE status='PENDING'`;
   only one worker's update matches, so overlapping workers cannot double-process a window. Expired
   leases (`lock_expires_at < now`) are recovered to `PENDING` at the start of each run. Portable
   across Postgres and the SQLite used by tests (all time comparisons are done in Python).
5. **Deterministic, configurable cadence & priority** (pure functions, unit-tested). Cadence bands:
   far-future/not-on-sale 24h, 15–30d 12h, final 14d 4h, on-sale first 48h 2h, event day 2h,
   post-event follow-ups at +1/+3/+7d, then stop. Falls back to event-date cadence when on-sale
   timing is unknown. Priority is an explainable component sum (urgency, on-sale burst, recent
   transition, priority city, failure penalty).
6. **Result classification keeps failure ≠ absence.** Results:
   `SUCCESS_RECORD_PRESENT | SUCCESS_RECORD_ABSENT | SOURCE_UNAVAILABLE | RATE_LIMITED | TIMEOUT |
   PARSER_FAILED | INVALID_RESPONSE | TERMINAL_EVENT`. Absence is emitted **only** on a *successful*
   request that reports the record gone (signal-service raises `EventNotFound` → HTTP 404). A failed
   request is retried with bounded exponential backoff (respecting `Retry-After`), never recorded as
   absence. Parser/invalid failures get limited retries then `NEEDS_REVIEW`.
7. **Shadow Ledger is the idempotency backstop.** A worker crash after capture but before job
   completion leaves an expired lease that is recovered and re-run; re-capturing unchanged state is a
   Shadow Ledger no-op, so no duplicate transitions arise.
8. **Default off.** `NQUARK_SCHEDULED_CAPTURE_ENABLED=false` by default; migrations run at boot only
   when enabled; the crawl-service scaffold's prior behaviour is otherwise unchanged. Boshow only in
   Phase 2 (`NQUARK_SCHEDULED_CAPTURE_SOURCES`).

## Consequences

- Boshow events accumulate longitudinal Shadow Ledger histories with no manual triggering.
- No public API and no crawl-space instrumentation are added; the operational-coverage surface is
  internal only.
- Not built (deferred): breadth-first crawling, multi-source reconciliation, market-coverage scores,
  and any commercial analytics. Out-of-order timeline reconciliation remains Phase 1.1's conservative
  audit-only behaviour.
