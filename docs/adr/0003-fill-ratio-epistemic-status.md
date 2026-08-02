# ADR 0003 — Boshow `fill_ratio` epistemic status

- Status: Accepted
- Date: 2026-08-02
- Phase: Phase 1 — Minimum Viable Shadow Ledger
- Relates to: [docs/ontology.md → Ticketing & demand ground truth](../ontology.md), signal-service ticketing adapter

## Context

Boshow exposes `tickets_sold / capacity` → `fill_ratio`. It is the strongest available demand signal,
but it is a **publicly displayed** value, not an audited sell-through. It must never be read as
verified paid sales / verified attendance.

## Decision

`fill_ratio` (and the `tickets_sold` / `capacity` it derives from) is classified:

```
epistemic_status = observed_public_state
```

It must NOT be labelled `verified_sell_through`, `verified_attendance`, or `verified_paid_sales`.
Preserved alongside the value: source, observed_at, source field name, reported value, provenance.

## Implementation (backward-compatible)

- No breaking change to the observation schema or analytics contracts. The observation-service
  `observations.metadata` column is already JSON, so `epistemic_status` is added **there** for the
  `fill_ratio` observation — additive, no migration, no contract change.
- In the Shadow Ledger, `epistemic_status` is a **first-class column** on `shadow_state` /
  `shadow_transition`, defaulting to `observed_public_state`.
- Enum (initial): `observed_public_state | reported_outcome | verified | model_estimate | unknown`.

## Consequences

- Existing analytics keep reading `fill_ratio` unchanged; the qualifier travels as metadata.
- Anything emitting `fill_ratio` downstream can (later) surface the qualifier without a schema change.
