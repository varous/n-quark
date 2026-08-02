# ADR 0004 — Scheduling dependency for repeated capture

- Status: Accepted
- Date: 2026-08-02
- Phase: Phase 1 — Minimum Viable Shadow Ledger
- Relates to: [docs/adapter-roadmap.md → Adaptive collection frequency](../adapter-roadmap.md), `deploy/fly/ingest-cron/`

## Context

Repeated capture drives transition detection. The autonomous, breadth-first crawl-service (with
adaptive frequency) is future work and must **not** be built in Phase 1.

## Decision

Phase 1 demonstrates repeated capture with the **existing** ingest/cron mechanisms:

```
manual repeated ingest · scheduled invocation via existing deploy tooling (deploy/fly/ingest-cron)
· fixture replay · a source-specific refresh (re-ingest) command
```

The transition detector is implemented as a **pure, storage-adjacent module + an internal HTTP
"observe" endpoint** so the future crawl-service can call it **without rewriting** the detector.

## Consequences

- No crawl-service work in this phase.
- `observe()` is idempotent on unchanged state, so any scheduler cadence is safe to over-call.
- Disappearance uses a **configurable** threshold of consecutive *authoritative* absences
  (`NQUARK_SHADOW_LEDGER_DISAPPEARANCE_THRESHOLD`), never a single failed capture — capture/parser/
  source failures are recorded but do not count toward `EVENT_DISAPPEARED`.
