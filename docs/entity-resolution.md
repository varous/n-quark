# Cross-inventory entity resolution (Phase 3.1)

Resolves platform-exclusive Boshow and District source events onto a **shared canonical entity graph**
— artists, venues, organizers and event series — so two exclusive catalogues become comparable through
the entities they share, without requiring the same event on both platforms and without collapsing
distinct source events. Deterministic and explainable only (no LLM). Lives in crawl-service; default off
(`ENTITY_RESOLUTION_ENABLED`).

## Why exact event overlap is not required

Ticketing platforms mostly carry exclusive inventory, so `same event on two platforms` is uncommon.
The convergence that matters is `different events → same artist / venue / organizer / recurring series
/ city`. Phase 3 duplicate-event reconciliation stays available opportunistically; Phase 3.1 is the
primary convergence layer.

## Source-event vs canonical-entity identity

- **Source handle** (`{source}:{type}:{slug}`) — a per-source identity for one mention. Durable evidence
  for future resolution; recorded in `entity_source_handle` → canonical id.
- **Canonical entity** — the cross-source identity, a graph node (`artist:…`, `venue:…--{city}`,
  `organizer:…`, `series:…`). Two source handles resolving to the same canonical id **is** cross-source
  convergence. A normalized name alone is never a permanent global id.

## Pipeline (per captured event, best-effort after enrichment)

```
capture (Phase 2) → enrich (Phase 2.1)
  → read canonical event node + neighbours
  → extract entity evidence (artist/venue/organizer/series)   [missing field => no evidence]
  → resolve each deterministically against known entities
  → persist candidate (audit) + source handle (registry) + history
  → write graph relationships (IDENTIFIES / FEATURES / OCCURS_AT / ORGANIZED_BY / PART_OF_SERIES)
  → classify outcome (SUCCEEDED / PARTIAL / AMBIGUOUS / NO_EVIDENCE / FAILED)
```

Failure never fails capture. Batch mode (`/run`) does the same over already-captured `tracked_event`s.

## Deterministic resolvers & the ambiguity policy

| Type | Resolves on | Refuses to auto-resolve |
|---|---|---|
| Artist | source handle, exact normalized name (feat./honorific/live-tail stripped, diacritics folded) | ambiguous single-token names (`King`, `Pilu`); a **tribute/cover act never collapses** into the original |
| Venue | source handle, name **+ city** (city-scoped id) | generic names (`Town Hall`, `The Club`) without geography; **same name in a different city stays distinct** (chains are location-specific); name without any city → UNRESOLVED (resolvable later) |
| Organizer | source handle, exact normalized name (Pvt Ltd/LLP/Productions/Events… stripped) | generic organizer tokens; never inferred from venue/sponsor/artist/platform (only the explicit organizer field is evidence) |
| Event series | series-normalized title (edition/year/vol/roman stripped) **+ organizer** | generic recurring titles (`Open Mic`, `Comedy Night`) without an organizer; **same title under a different organizer does not link**; edition number never inferred from order |

Every resolver returns `status / canonical_entity_id / score / supporting / contradicting / reason_code
/ resolver_version`. Reason codes: `SOURCE_HANDLE_MATCH`, `EXACT_UNIQUE_ALIAS`, `NAME_AND_CITY_MATCH`,
`GENERIC_NAME`, `AMBIGUOUS_NAME`, `MULTIPLE_CANDIDATES`, `TRIBUTE_OR_COVER_ACT`, `VENUE_HAS_NO_GEOGRAPHY`,
`INSUFFICIENT_EVIDENCE`, `NEW_CANONICAL_ENTITY_CREATED`. A RESOLVED decision below the entity's
configured auto-resolve threshold is downgraded to POSSIBLE_MATCH.

## Interaction with event reconciliation

Shared entities may improve Phase 3 matcher signals but **never auto-create an event match**. Two events
featuring the same artist, or two editions of a series, are still different events. Proven live: two
shared-artist events in different cities converge to one canonical artist yet reconcile to 0 matches.

## Venue geography failure modes

`NO_VENUE_TEXT` · `UNRESOLVED_VENUE` · `AMBIGUOUS_VENUE` · `VENUE_HAS_NO_GEOGRAPHY` ·
`DIRECT_SOURCE_GEOGRAPHY_ONLY` — recorded per event so a geographic-enrichment miss is explainable.

## Internal endpoints (flag-gated; no public API)

- `POST /v1/internal/entity-resolution/run?sources=&limit=&trace=`
- `GET /v1/internal/entity-resolution/coverage?source=`
- `GET /v1/internal/entity-resolution/cross-inventory?entity_type=` — convergence proof
- `GET /v1/internal/entity-resolution/unresolved?entity_type=&source=`
- `GET /v1/internal/entity-resolution/candidates/{id}` — decision + history
- `GET /v1/internal/entities/{entity_type}/{entity_id}/source-handles`
- `GET /v1/internal/events/{event_id}/resolved-entities`

## Config (defaults off)

`ENTITY_RESOLUTION_ENABLED` (false), `ENTITY_RESOLUTION_SOURCES` (`boshow,district`),
`ARTIST_AUTO_RESOLVE_THRESHOLD` (0.8), `VENUE_AUTO_RESOLVE_THRESHOLD` (0.8),
`ORGANIZER_AUTO_RESOLVE_THRESHOLD` (0.75), `EVENT_SERIES_AUTO_RESOLVE_THRESHOLD` (0.7),
`ENTITY_RESOLUTION_MAX_EVENTS_PER_RUN` (500).

## Known limitations

- Cross-source convergence needs sources whose catalogues actually overlap; the current Boshow/District
  cohorts are disjoint (live cross-source count = 0). Mechanics proven by fixtures (labeled).
- Deterministic year-marker series detection has false positives (e.g. "F1 2026 …" reads a product year
  as an edition). Harmless — a distinct series node, event preserved.
- Short but legitimate single-token names (e.g. "Pilu") are queued AMBIGUOUS rather than risk a wrong
  merge — precision over recall by design.
- The ingest-time entity projection (naive name-slug in signal-service) still writes its own
  FEATURES/OCCURS_AT edges; Phase 3.1 adds the corrected, evidence-based canonical layer and handle
  registry alongside it. Unifying the two id conventions is a documented follow-up.
- No address/geocoding source yet, so venue geography is `DIRECT_SOURCE_GEOGRAPHY_ONLY` (from the event).
- Deferred (not in scope): another ticketing source, poster OCR, social/organizer-site crawling, LLM
  resolution, canonical **event** merging / duplicate-event write-back, demand/sales scoring, a review UI.
