# ADR 0009 — Independent second source (District) + cross-platform reconciliation (Phase 3)

- Status: Accepted
- Date: 2026-08-04
- Phase: Phase 3 — Independent Second-Source Capture and Cross-Platform Event Reconciliation
- Extends: [ADR-0006](0006-scheduled-capture.md), [ADR-0007](0007-capture-enrichment.md), [ADR-0008](0008-source-family-and-pilot.md)
- Relates to: [docs/shadow-ledger.md](../shadow-ledger.md)

## Source-selection probe (live, 2026-08-04)

Both candidates were probed live before selection:

| | District | Skillbox |
|---|---|---|
| sitemap | 200, **5,440** events | 200, **24,299** events |
| event surface | SSR page, **6 schema.org JSON-LD blocks**, ~0.57 s | JSON API, ~0.37 s |
| structured fields | **name, startDate, endDate, location(+address), eventStatus, offers(price), organizer, image** | flat JSON: `event_display_name`, `date_from/to`, `min/max_price`, **numeric `city_id` (no city name)**, `cover_image`; **no clean venue name**, placeholder dates ("To Be Announced") |

**Selected: District.** It exposes rich, standard schema.org metadata (exactly the fields
reconciliation needs — start/end/venue/city/organizer/status/offers) via a stable SSR surface, has a
genuinely independent origin, and its adapter (`event_from_district`) already parses it. Skillbox's
flat API gives a numeric `city_id` (no name), no clean venue, and placeholder dates — much weaker for
field reconciliation. (Neither overlaps Boshow's tiny Kolkata-grassroots dev cohort; that is expected.)

## Decisions

1. **One route, per-request provider.** signal-service's ticketing `discover`/`preview`/`ingest`
   accept an optional `source`, so the existing scheduler captures Boshow and District through one
   pipeline — no parallel scheduler. District `extract` raises `EventNotFound` on a reachable page
   with no Event JSON-LD, so absence is authoritative and a failed request never becomes absence.
2. **Per-origin independence.** A candidate's `independence_group` derives from its **originating
   platform** (`boshow_origin` / `district_origin` / `nquark_temporal`), not its surface. A canonical
   graph projection inherits its source's group. Consensus (`RESOLVED_CONSENSUS` + full boost) is
   granted only across **different** independence groups, so Boshow + District agreement is real
   consensus while Boshow API + Boshow page is not.
3. **Bounded blocking, deterministic matcher.** Candidates are only generated within blocks (date
   within tolerance, compatible city, shared title/performer/venue signal). The pure matcher scores
   title / performer / venue / city / date / organizer, and **refuses to auto-match when a strong
   contradiction exists** (different city, date beyond tolerance, non-overlapping performers) — a high
   title similarity never overrides. Auto-match needs score ≥ threshold, ≥2 meaningful agreeing
   dimensions, a compatible date, and different sources; otherwise `POSSIBLE_MATCH`/`CONFLICT`/`NOT_MATCHED`.
4. **Linkage without truth collapse.** An accepted match links two source listings via
   `event_match_candidate` (`REPRESENTED_BY`) — both source records, their Shadow Ledger histories,
   enrichment candidates, and displayed prices/availability are preserved. Canonical ids are **not**
   merged.
5. **Field reconciliation across independence groups.** After a match, the existing resolver runs over
   both sources' candidates: independent agreement → consensus; one source fills a field the other
   lacks; conflicts stay explicit and never silently update scheduling metadata; source-specific
   prices/availability are compared and classified (`PLATFORM_DIFFERENCE` / `SAME` / `SINGLE_SOURCE`),
   not flattened.
6. **Default off, additive.** Migration `004` (`event_match_candidate`); `SECOND_SOURCE_CAPTURE_ENABLED`
   / `RECONCILIATION_ENABLED` default off; Boshow-only behaviour unchanged when disabled.

## Consequences

- District becomes the first source that can produce *true* cross-platform consensus and genuine
  incremental fields (`end_at`, `event_status`, organizer, richer venue/address).
- Live Boshow↔District overlap is ~0 in the current cohort (grassroots vs mainstream), so match
  mechanics are proven by fixture-backed tests (per the phase's allowance), not a fabricated live match.
