# ADR 0007 — Evidence-based capture enrichment (Phase 2.1)

- Status: Accepted
- Date: 2026-08-03
- Phase: Phase 2.1 — Evidence-Based Capture Enrichment
- Extends: [ADR-0006](0006-scheduled-capture.md), [ADR-0001](0001-shadow-ledger-raw-evidence-retention.md)
- Relates to: [docs/shadow-ledger.md](../shadow-ledger.md)

## Context

Phase 2 enrolled Boshow events but couldn't back-fill date/venue/city, so cadence stayed on the
conservative `no_event_date` fallback. Enrichment must fill these from trustworthy evidence without
assuming the Boshow adapter exposes every field, and without ever writing a guessed value into a
canonical record.

## Decisions

1. **Candidate → resolution separation.** Evidence produces `enrichment_candidate` rows (one per
   field-value per surface, with full provenance); a deterministic resolver produces one *current*
   `event_field_resolution` per field. Weakly-inferred values are never written straight to canonical
   records.
2. **Field-specific resolution.** Different fields resolve from different evidence at different
   authorities (an authoritative field registry defines, per field: normalizer, allowed source types,
   precedence, min auto-confidence, whether derived resolution is allowed, scheduler relevance).
3. **Two highest-confidence paths only** (this phase): (a) **canonical graph relationship** — the
   event's graph node carries `starts_at`/`city` (Boshow structured fields) and `OCCURS_AT`→venue /
   `IN_REGION`→region, so venue/city/region are *derived* (`CANONICAL_RELATIONSHIP`), never asserted
   as a direct source field; (b) **Boshow public-page structured metadata** — JSON-LD (title-matched,
   not "first Event"), embedded state, Open Graph, labelled visible text. Cross-platform matching,
   poster OCR, social/press/search enrichment, and a review UI are **deferred**.
4. **No guessed values.** A missing or unparseable field produces **no candidate** (never a null).
   Parse failure is an extraction failure, not `OBSERVED_NULL`.
5. **Deterministic resolver (no LLM).** Higher authority beats lower; independent agreement raises
   confidence; equal-authority disagreement is `CONFLICT` (unresolved, flagged); a stale/lower value
   never overrides a newer higher-authority one; below min-confidence → `NEEDS_REVIEW`; no evidence →
   unresolved (unknown), never guessed.
6. **On-sale timing is never an invented point.** `source_on_sale_at` only from an explicit source
   timestamp; a not-on-sale→on-sale observation yields an interval
   (`estimated_on_sale_window_start/_end`); a first-already-on-sale observation records only
   `first_ticket_state_seen_at`.
7. **Resolution history preserved.** A changed resolved value (e.g. a reschedule) creates a new
   version and marks the prior `is_current=False`; earlier resolutions + their evidence are never
   overwritten. The Shadow Ledger continues to detect the effective event-date change independently.
8. **Scheduler updates only from resolved fields.** `tracked_event` is updated only from
   auto-resolved fields; partial enrichment never erases existing values; conflicts never update
   scheduling metadata; a date/status change recalculates `next_capture_at`; the city allow-list is
   re-checked. Enrichment is **best-effort** and never fails the commercial-state capture.
9. **Retention** follows ADR-0001: candidates store extracted values + hashes + evidence references
   (source URL), not full third-party HTML.
10. **Default off.** `NQUARK_CAPTURE_ENRICHMENT_ENABLED=false`; public-page fetch separately gated by
    `NQUARK_CAPTURE_ENRICHMENT_PUBLIC_PAGE_ENABLED`.

## Consequences

- Enrolled events gain real timing/geography with candidate-level provenance, so the Phase 2 cadence
  actually engages; the canonical graph path works live, the public-page path is Boshow-specific.
- Additive migration `002` (two tables + nullable `tracked_event` columns); public feed, analytics,
  and Shadow Ledger semantics are unchanged.
- Deferred enrichment sources are documented as future source types, not built.
