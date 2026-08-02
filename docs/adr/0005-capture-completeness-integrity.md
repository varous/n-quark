# ADR 0005 — Capture completeness & transition integrity (Phase 1.1)

- Status: Accepted
- Date: 2026-08-02
- Phase: Phase 1.1 — Capture Completeness and Transition Integrity
- Extends: [ADR-0004](0004-scheduling-dependency.md) (disappearance), [ADR-0003](0003-fill-ratio-epistemic-status.md)
- Relates to: [docs/shadow-ledger.md](../shadow-ledger.md)

## Context

The Phase 1 detector compared normalized states directly, so a field simply *missing* from a later
capture could read as a real transition to `null` (e.g. an omitted `starts_at` → false
`EVENT_DATE_CHANGED`). Before scheduled repeated capture begins, every future capture must be
trustworthy.

## Decisions

1. **Snapshot completeness** — every capture is `COMPLETE` or `PARTIAL`. Callers that don't declare
   it default to **`PARTIAL`** (conservative). Unknown callers are never treated as complete.
2. **Field-level observation status** — `OBSERVED_VALUE | OBSERVED_NULL | NOT_OBSERVED |
   EXTRACTION_FAILED | NOT_SUPPORTED`. Only `OBSERVED_VALUE`/`OBSERVED_NULL` can produce a
   transition. When a status is not supplied it is **inferred**: a concrete value → `OBSERVED_VALUE`,
   anything else (missing or `None`) → `NOT_OBSERVED`. A model default of `None` is **never**
   inferred as `OBSERVED_NULL`.
3. **Effective-state merge** — the new effective state = previous effective state with only the
   validly-observed fields overlaid. Unobserved/failed/unsupported fields are **carried forward**,
   never nulled. Carried-forward fields are recorded as `NOT_OBSERVED` in the capture's `field_status`
   (we never claim they were observed again).
4. **Explicit null** — a value→null transition is emitted only for `OBSERVED_NULL` on a field whose
   registry entry sets `explicit_null_allowed`. Phase 1.1 permits it for `availability` and
   `status` only; for other fields an `OBSERVED_NULL` is suppressed (`EXPLICIT_NULL_NOT_ALLOWED`) and
   the previous value is kept.
5. **Two hashes** — `capture_hash` (what this capture observed) and `effective_state_hash` (the
   resulting merged state, stored under the existing `state_hash` column). Idempotency/no-op is on the
   effective-state hash for present captures; equal-timestamp dedup uses the capture hash.
6. **Out-of-order** (conservative, no timeline recomputation in Phase 1.1) — a capture older than the
   current latest, or an equal-timestamp capture with a different payload, is persisted flagged
   `out_of_order=true` for audit and **excluded** from current-state and forward-transition
   emission. Equal timestamp + identical payload is idempotent. Timeline reconciliation is future
   work.
7. **Disappearance is capture-status-driven** — `capture_status`
   (`CAPTURE_SUCCESS_RECORD_PRESENT | CAPTURE_SUCCESS_RECORD_ABSENT | SOURCE_UNAVAILABLE |
   CAPTURE_FAILED | PARSER_FAILED | NOT_CHECKED | EXPLICITLY_REMOVED`). Only authoritative absence
   (`CAPTURE_SUCCESS_RECORD_ABSENT`, `EXPLICITLY_REMOVED`) counts; failures never increment and are
   not persisted as states. `EVENT_DISAPPEARED` fires once at the configured consecutive-absence
   threshold (or immediately on explicit removal); a present capture resets the counter and, if the
   event had disappeared, emits one `EVENT_REAPPEARED`.

## Consequences

- Partial/failed/out-of-order captures can no longer fabricate transitions or corrupt current state.
- Schema change is additive (migration `003`, all columns nullable/defaulted); existing rows and the
  public `/v1/events` + analytics contracts are unchanged.
- The internal `observe`/inspection responses gain additive fields (completeness, field_status,
  capture_status, hashes, out_of_order, absence_count, suppressed transitions).
- `fill_ratio` remains `observed_public_state` (ADR-0003), unchanged.
