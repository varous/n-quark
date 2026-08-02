# ADR 0001 — Shadow Ledger raw-evidence retention (provisional)

- Status: Accepted (provisional — revisit after a rights & retention review)
- Date: 2026-08-02
- Phase: Phase 1 — Minimum Viable Shadow Ledger
- Relates to: [product-spec.md → Independent Market Observation and Temporal Data Moat](../product-spec.md#independent-market-observation-and-temporal-data-moat)

## Context

The moat needs longitudinal evidence, but the existing media policy favours hotlinking and defers
re-hosting third-party content pending a rights review (unchanged by this ADR).

## Decision

For unlicensed third-party public sources, the Shadow Ledger stores **only**:

```
source URL · capture timestamp · HTTP response metadata (where permitted) · content hash ·
normalized extracted fields · field-level state hash · image URL · image perceptual hash
(where already supported & lawful) · provenance envelope
```

It does **not** store, in Phase 1: full third-party HTML, screenshots, downloaded posters, or copied
copyrighted page content. Phase 1 must not depend on full-page archival.

Full HTML / screenshots may be supported **later** only behind: a source-specific policy, a retention
duration, a stated legal basis, a redistribution prohibition, and an explicit configuration flag.

Test fixtures already committed to the repo remain, per existing test practice.

## Consequences

- The Shadow Ledger `shadow_state` record persists `normalized_state` + `state_hash` + provenance,
  not raw pages. `raw_snapshot_reference` (a pointer/URL) is supported; raw blobs are not stored.
- Reversible: no raw-content store is introduced, so nothing to purge if the policy tightens.
