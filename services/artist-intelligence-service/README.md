# artist-intelligence-service (Phase 5A)

n-quark's **public demand-side** intelligence layer. It attaches external platform identities (YouTube
channels, Google Trends terms/topics) to **existing canonical artists** and records their public demand
over time, then juxtaposes that demand against observed live **supply** — through `canonical_artist_id`
only.

Two evidence systems, kept strictly separate:

```
EVENT SUPPLY   Boshow / District → event observations → Shadow Ledger   (owned by graph/crawl)
PUBLIC DEMAND  YouTube / Google Trends → artist demand observations      (owned here)
                                    ↘ meet only at canonical_artist_id ↙
```

YouTube/Trends metrics are **never** written into the event Shadow Ledger, and this service **never
creates a canonical artist** — identity stays owned by the entity/graph architecture.

## Design

- **No parallel ingestion path.** All YouTube fetching (search / channel / videos), the API key, and mock
  modes live in **signal-service** (the existing ingestion layer, extended with acquisition-only `search`
  + `videos` endpoints). This service's YouTube "provider" is a thin client that calls signal-service —
  like analytics-service calls crawl/graph. There is no second YouTube client anywhere.
- **Separate service, to protect the collection spine.** Demand persistence + the refresh scheduler live
  here, not inside signal-service, so a demand-layer failure never disrupts the live crawl→signal event
  collection (see [ADR-0017](../../docs/adr/0017-demand-observation-ledger.md)).
- **Descriptive, not predictive.** Deterministic read models only. No composite popularity/value/booking
  score; supply and demand are juxtaposed, never fused. Temporal co-movement is reported without any
  causal claim.

## Data model (own tables; own alembic version table `alembic_version_artist_intel`)

| table | purpose |
|---|---|
| `artist_external_identity` | a canonical artist's identity on a platform; RESOLVED/AMBIGUOUS/UNRESOLVED |
| `artist_demand_observation` | append-only temporal/geographic demand facts; idempotent on `observation_key` |
| `provider_quota_day` | per-provider/day request + quota accounting (search vs non-search units) |
| `demand_refresh_job` | lease-locked, restart-safe refresh queue |

## Internal API (internal-only; no public surface)

```
GET  /v1/internal/artists/{id}/external-identities
POST /v1/internal/artists/{id}/youtube/resolve      GET /v1/internal/artists/{id}/youtube
POST /v1/internal/artists/{id}/youtube/refresh
POST /v1/internal/trends/import                     GET /v1/internal/artists/{id}/trends
GET  /v1/internal/artists/{id}/demand               GET /v1/internal/artists/{id}/momentum
GET  /v1/internal/artists/{id}/geography            GET /v1/internal/artists/{id}/event-response?event_id=
GET  /v1/internal/artists/{id}/observations         (date_from,date_to,provider,metric,scope_id,limit,offset)
GET  /v1/internal/demand/coverage                   GET /v1/internal/demand/provider-health
GET  /v1/internal/demand/quota
```

## Configuration (all OFF by default; `NQUARK_` prefix)

| var | default | meaning |
|---|---|---|
| `DEMAND_INTELLIGENCE_ENABLED` | false | master switch; enabling runs migrations on boot |
| `YOUTUBE_ENABLED` / `YOUTUBE_SEARCH_ENABLED` | false / true | YouTube via signal-service; search is bounded |
| `YOUTUBE_MAX_SEARCHES_PER_DAY` | 50 | daily search budget (search = 100 quota units) |
| `YOUTUBE_CHANNEL_REFRESH_INTERVAL_SECONDS` | 86400 | channel snapshot cadence (daily) |
| `GOOGLE_TRENDS_MODE` | IMPORT | `OFFICIAL_API` (needs alpha creds → else ACCESS_UNAVAILABLE) \| `IMPORT` \| `DISABLED` |
| `DEMAND_SCHEDULER_ENABLED` | false | run the in-process refresh loop |
| `SIGNAL_SERVICE_URL` / `GRAPH_SERVICE_URL` / `CRAWL_SERVICE_URL` | docker/local | upstream reads |

The YouTube API key lives in **signal-service** secrets, never here, never in git or logs.

## Local dev

```bash
DEMAND_INTELLIGENCE_ENABLED=true YOUTUBE_ENABLED=true \
  docker compose up -d --build artist-intelligence-service
curl -s localhost:8010/health
```

Tests: `PYTHONPATH=. pytest` (SQLite; providers faked — no network). See
[docs/demand-intelligence.md](../../docs/demand-intelligence.md),
[docs/providers/youtube.md](../../docs/providers/youtube.md),
[docs/providers/google-trends.md](../../docs/providers/google-trends.md).
