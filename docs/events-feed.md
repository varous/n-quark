# Events feed — n-quark → crawl-space

The read-only feed crawl-space syncs from. n-quark ingests events for *intelligence*; this feed
is the controlled surface for *redistribution*. The redistribution policy is enforced here
(server-side), so crawl-space just consumes what it's handed.

## Endpoint

```
GET {gateway}/v1/events
```

Served by graph-service, exposed through the api-gateway (CORS enabled there). Read-only.

### Query parameters

| Param | Type | Notes |
|---|---|---|
| `tier` | string | `open` \| `link_only` \| `excluded`. Omit to get everything **except** `excluded`. |
| `free` | bool | `true`/`false` — filter by free vs paid. |
| `source` | string | `boshow` \| `district` \| `skillbox` \| `townscript` \| `luma` \| `meetup` \| `knowafest` \| `allevents`. |
| `city` | string | case-insensitive exact match. |
| `updated_since` | ISO 8601 | **incremental sync** — only events written at/after this instant. |
| `limit` | int | 1–200 (default 50). |
| `offset` | int | pagination. |

### Response

```json
{ "count": 42, "limit": 50, "offset": 0, "events": [ EventFeedItem, ... ] }
```

`EventFeedItem`:

| Field | Meaning |
|---|---|
| `id` | canonical event id (`event:<slug>`) — dedup key across sources |
| `name`, `category`, `city`, `region`, `venue` | display fields |
| `venue_id`, `region_id` | **canonical, deduped ids** (`venue:<slug>`, `region:<slug>`) — key communities on these, not on the display strings |
| `organizer` | organizer/curator name (not a canonical entity yet — name only) |
| `artists`, `artist_ids` | lineup display names + their canonical `artist:<slug>` ids |
| `starts_at`, `price_min`, `currency`, `is_free` | when / cost |
| `fill_ratio` | tickets sold ÷ capacity, where the platform exposes it (Boshow); else null |
| `image_url` | platform-hosted poster (hotlink; not re-hosted) |
| `source`, `source_url` | origin platform + the page to link out to |
| `redistribution_tier` | `open` \| `link_only` \| `excluded` (see below) |
| `updated_at` | last write — use as the next `updated_since` cursor |

## Redistribution tiers — how crawl-space must treat each

| Tier | What | crawl-space treatment |
|---|---|---|
| **open** | free anything + grassroots/community (Boshow, Townscript, Luma, Meetup, Knowafest) | render as a full card |
| **link_only** | mainstream ticketing paid (District/Skillbox) + aggregators (AllEvents) | show as discovery, **link out via `source_url`** — do not intercept the sale |
| **excluded** | unverified / spam | not exported (only visible with explicit `?tier=excluded`, for review tooling) |

Posters are the platforms' own hosted URLs — hotlink for display; do not re-host (that's a
separate media/legal decision). If a poster fails to load, fall back to a placeholder.

## Sync recipe

Incremental, idempotent, into crawl-space's own DB:

1. Keep a cursor = the max `updated_at` seen last run (start empty for a full backfill).
2. `GET /v1/events?updated_since=<cursor>&limit=200&offset=0`, paging on `offset` until `count` is exhausted.
3. **Upsert by `id`** — the canonical id is stable and deduped across sources.
4. Store `redistribution_tier`; gate rendering on it (`open` → card, `link_only` → link-out).
5. Advance the cursor to the max `updated_at` in the batch.

A runnable reference implementation is in [`tools/crawl_space_sync.py`](../tools/crawl_space_sync.py)
(stdlib only; upserts into a local SQLite to demonstrate — adapt the `upsert` to crawl-space's
schema/migrations).
