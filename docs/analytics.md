# Analytics — canonical market read models (Phase 4A)

Deterministic, bounded read models over the **observed** Boshow + District data. They answer questions
like *how many observed events in Kolkata this month, which artists appear most, which venues are most
active, which organizers span venues/platforms, how longitudinal and how fresh the dataset is* — with
**no prediction, no scores, and no total-market claim**. Every count is by **canonical entity id**.

All endpoints are `GET`, under `/v1/analytics/market`, and served by analytics-service (`:8007`).

## Common query facets

| Param | Applies to | Meaning |
|---|---|---|
| `date_from`, `date_to` | all | filter events by start time (ISO 8601) |
| `source` | all | `boshow` \| `district` |
| `city` | all | exact city match (case-insensitive) |
| `region` | all | region node id (e.g. `region:west-bengal`) |
| `trace` | all | `true` adds a `trace` block (see below) |
| `limit`, `offset` | list endpoints | pagination (default 50, max 200); stable sort |
| `by` | observation-quality | `source` \| `region` breakdown |

Every response includes a `scope` block: `observation_scope`, `sources`, `as_of`, and `limitations`.

## Endpoints

### Canonical projection
`GET /market/canonicalize/{entity_id}` → `{input_entity_id, canonical_entity_id, resolution_path,
identity_state, warnings}`. Folds a legacy/superseded id onto its canonical (following `SUPERSEDED_BY`
then alias edges), with cycle/invalid-chain warnings. Non-destructive.

### Regions — observed supply
`GET /market/regions` (list) · `GET /market/regions/{region_id}` (detail). Grouped by region node, or
`city:{city}` when an event has no region. Per group: observed / upcoming / completed / cancelled /
undated event counts, unique canonical artists/venues/organizers, source distribution, events with vs
missing resolved geography. Detail adds `by_city`, `event_ids`, and named artist/venue lists.

### Artist / venue / organizer activity
`GET /market/artists[/{artist_id}]`, `/market/venues[/{venue_id}]`, `/market/organizers[/{organizer_id}]`.
Each detail returns observed/upcoming/completed counts, cities, regions, source distribution,
first/last observed, events with longitudinal history, and the linked canonical entities. Venues add
`categories`, `geography_provenance` (`DIRECT_SOURCE_GEOGRAPHY_ONLY`) and `events_with_state_transitions`.
Organizers add `source_usage`, `event_series` and deterministic `recurrence_indicators` (multi-venue /
multi-city / has-series — indicators, **not** a score).

### Event series
`GET /market/series[/{series_id}]`. Only **strong** canonical series (edition/volume/season/roman — the
Phase B safeguard excludes weak/year-only) that are not superseded. Returns `edition_count`,
`linked_event_ids`, organizer, cities, venues, source distribution, first/last edition observed.

### Observation quality
`GET /market/observation-quality[?by=source|region]`. Tracked events, captured successfully, 2+/3+
observations, 2+ distinct states, 1+ transitions, average capture gap, stale, partial/failed/out-of-order
captures, events with unresolved entities, events missing date/venue/geography. Measures **observation
quality, not market coverage**.

### Commercial state
`GET /market/commercial-state`. From observed Shadow Ledger facts only: events with price observations /
price changes / availability changes / status changes / date-or-venue changes / disappeared-or-reappeared;
`displayed_price` distribution (min/max/median); `price_by_source` kept **separate** per source;
`time_to_event_hours`. **No sales, capacity or sell-through is estimated** unless directly observed.

## Trace (`trace=true`)

```
source_events_included / source_events_excluded (with reason)
canonical_resolution_paths      # every legacy id folded to its canonical, with the path + warnings
superseded_identities_deduplicated
missing_field_exclusions        # events missing starts_at / city / venues
metric_definitions
observation_quality_warnings
```

## Guarantees

- Deterministic and explainable; no LLM, no prediction, no demand/popularity scoring.
- Entities counted by canonical id; legacy/superseded folded, never double-counted. Unmerged
  legacy/canonical duplicates **without** a `SUPERSEDED_BY` edge are reported, never silently merged.
- Read-only; no destructive graph migration. Raw source + Shadow Ledger stay authoritative.
- Bounded + paginated; results state their observation scope and limitations.
