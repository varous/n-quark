# ADR 0002 — Redistribution of Shadow Ledger transitions & estimates

- Status: Accepted
- Date: 2026-08-02
- Phase: Phase 1 — Minimum Viable Shadow Ledger
- Relates to: [docs/events-feed.md](../events-feed.md), [product-spec.md](../product-spec.md#independent-market-observation-and-temporal-data-moat)

## Context

The existing `/v1/events` feed (graph-service, consumed by crawl-space) redistributes *current* event
state under tier rules (open / link_only / excluded). The Shadow Ledger adds *historical* state and
transitions. Their redistribution posture must be explicit before they exist.

## Decision

```
Canonical current event state ............ existing redistribution rules apply (unchanged)
Historical Shadow Ledger transitions ..... internal / restricted by default
Derived commercial classifications ....... internal / restricted by default
Estimated sell-through / occupancy ....... internal / restricted by default
Verified or source-reported public claims  may be exposed ONLY under a source-specific policy
```

- **`/v1/events` output is NOT modified in Phase 1.** No new fields, no tier changes.
- Shadow Ledger data is exposed only via an **internal** surface: `GET /v1/internal/events/{id}/shadow-ledger`.
- Each `shadow_state` / `shadow_transition` row carries `epistemic_status`; nothing is exported to the
  public feed regardless.

## Consequences

- The public feed contract and its tests are unaffected.
- A future public/partner surface for transitions requires its own ADR + redistribution tiering.
