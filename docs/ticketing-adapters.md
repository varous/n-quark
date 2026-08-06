# Ticketing adapters — shared contract (Phase 4C)

Every ticketing source is acquired behind **one typed contract** so downstream services consume only the
normalized `TicketingEvent` — never a source-specific shape. This is additive over the existing pluggable
providers (`TicketingProvider`: discover/extract); `BaseTicketingAdapter` wraps a provider and adds the
rest of the contract from the module-level normalization already proven for Boshow/District.

## The contract (`adapters/contract.py`)

```python
class TicketingAdapter(Protocol):
    source: str
    async def discover(city, limit) -> list[str]
    async def fetch_event(event_ref) -> TicketingEvent          # EventNotFound => authoritative absence
    def normalize_event(event) -> list[NormalizedObservation]
    def classify_failure(exc) -> str
    def extract_source_handles(event) -> dict                    # event/venue/organizer/artists/region + series evidence
    def extract_asset_references(event) -> list[dict]            # POSTER image ref for media-service
```

**Failure classes** (`classify_failure`, aligned with the scheduler's result codes):
`SUCCESS_RECORD_ABSENT` (EventNotFound / HTTP 404), `RATE_LIMITED` (429), `BLOCKED` (401/403),
`SOURCE_UNAVAILABLE` (timeout / network / 5xx), `MALFORMED_RESPONSE` (parse error), `TERMINAL_RECORD_ERROR`.

**Source handles** are evidence only — resolution to canonical ids happens later (entity resolution), and
a shared handle never implies a duplicate event.

## Sources

| Source | Discovery | Extraction | Identity | Notes |
|---|---|---|---|---|
| **Boshow** | `/api/search` (form-encoded) | same search record | `boshow:show:{show_id}` | Kolkata grassroots; uniquely exposes `tickets_sold`+capacity (fill_ratio) |
| **District** | events sitemap | schema.org JSON-LD | slug | nationwide mainstream; JSON-LD |
| **Skillbox** | `sitemap-event.xml` | `POST event-details {slug}` | `EventId` (stable) | third-source pilot; see the probe report below |

Boshow/District conform via `get_adapter(source)` with **no behavioural change** — existing source ids,
graph/Shadow-Ledger writes, image extraction and failure semantics are preserved (contract-compliance
tests assert this).

## Quality validation (`adapters/quality.py`)

Applied **before** enrollment into scheduled capture. Deterministic, conservative (precision over recall),
never fabricates values, never guesses coordinates. Rejection reasons:

`MISSING_IDENTITY`, `PLACEHOLDER_EVENT`, `PLACEHOLDER_DATE`, `INVALID_DATE`, `GENERIC_LOCATION`,
`NUMERIC_LOCATION_WITHOUT_MAPPING`, `MULTIPLE_CITIES_PLACEHOLDER`, `MALFORMED_RECORD`, `CONTENT_NOT_EVENT`,
`SEO_OR_SPAM_PAGE`, `DELETED_EVENT_SHELL`, `UNSUPPORTED_EVENT_TYPE`.

- **Geography**: distinguishes venue / locality / city / region; maps internal ids only through an explicit
  `VERIFIED_CITIES` map (never a numeric id as a city); rejects `Multiple/Mutiple Cities`; keeps
  direct-source region separate from derived region; no coordinate guessing.
- **Date/timezone**: normalizes to tz-aware using the source tz, else the verified-city tz **only**;
  leaves a naive time naive (with a warning) when the city is unverified; flags far-future placeholders
  (`>= now.year + 3`); never converts undated pages into scheduled events.
- **Field status**: reports `present` / `valid` / `specific` per field (resolved is a capture-time metric).

## Validated discovery + diagnostics (`adapters/sources.py`, `routes/sources.py`)

`validated_discovery(source, city, limit)` discovers candidates, fetches each (bounded by
`quality_fetch_cap`), validates and city-filters — accepted records are enrollable, rejected records carry
reasons, out-of-city records are `out_of_scope`. Internal endpoints (bounded, no raw HTML):

```
GET /v1/internal/sources
GET /v1/internal/sources/{source}/quality|rejections|coverage|sample|validated-discovery
```
Filters: `city`, `rejection_reason`, `limit`, `offset`. Discovery-time metrics only — capture-time metrics
(record-present, transitions, gap, entity-resolution rate) come from the capture pipeline
(crawl-service + the admin BFF). Always labelled **observed supply**, never total-market coverage.

## Configuration (defaults off)

`SKILLBOX_ENABLED`, `SKILLBOX_DISCOVERY_ENABLED`, `SKILLBOX_CITY_FILTERS` (`Kolkata`),
`SKILLBOX_DISCOVERY_LIMIT`, `SKILLBOX_CAPTURE_TIMEOUT`, `SKILLBOX_RATE_LIMIT`, `SKILLBOX_RETRY_LIMIT`,
`SKILLBOX_SITEMAP_URL`, `TICKETING_MANAGED_SOURCES`, `QUALITY_FETCH_CAP`. Boshow/District behaviour is
unchanged when Skillbox is disabled. Enrolling Skillbox into the pipeline is config-driven — add
`skillbox` to the crawl `SCHEDULED_CAPTURE_SOURCES` / `ENTITY_RESOLUTION_SOURCES` /
`MEDIA_OBSERVATION_SOURCES` lists; it then runs the identical discovery → validation → tracked_event →
capture → Shadow Ledger → enrichment → entity resolution → media path (entity + media hooks best-effort).

See [ADR-0015](adr/0015-shared-ticketing-adapter-contract.md), [skillbox-probe.md](skillbox-probe.md),
and [soundcharts-feasibility.md](soundcharts-feasibility.md).
