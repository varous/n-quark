# Provider: YouTube (Data API v3) — Phase 5A

Public, arbitrary-artist demand intelligence via the **official YouTube Data API v3**, with an API key
only (no channel-owner OAuth). Acquisition is delegated to **signal-service** (the single ingestion
path); artist-intelligence-service adds identity resolution, persistence, quota accounting, and read
models on top. The API key lives in signal-service secrets — never in artist-intelligence-service, git,
or logs.

## Acquisition primitives (in signal-service)

| endpoint | YouTube call | quota | used for |
|---|---|---|---|
| `GET /v1/signals/youtube/search?q=` | `search.list` (type=channel) | **1 unit, independent Search-Queries quota (100 calls/day)** | identity discovery only |
| `GET /v1/signals/youtube/channels/{id}/preview` | `channels.list` | 1 unit (general pool) | channel snapshot |
| `GET /v1/signals/youtube/channels/{id}/videos/preview` | `channels.list`+`playlistItems.list`+`videos.list` | ~3 units (general pool) | recent videos |

> **Quota model (current, post-June-2026 — 5B.2.7).** `search.list` is metered in a SEPARATE
> "Search Queries" quota: 1 unit/call, default 100 calls/day. It is INDEPENDENT of the general
> 10,000-unit/day pool used by `channels.list` / `playlistItems.list` / `videos.list` (1 unit each). The
> two quotas are never summed into one usage total, and a search request is never double-counted into
> the general pool. The obsolete `search.list = 100 general units` model has been removed.

Runs in a deterministic mock when no API key is set, so the whole pipeline is demonstrable offline.

## Identity verification invariant (Phase 5A.1a)

> **YouTube search is candidate discovery only. A CHANNEL_ID may transition to RESOLVED only after an
> authoritative `channels.list` lookup confirms that exact provider id exists at resolution time.**

Search-result evidence (title, topic signal, handle) is never sufficient on its own — a search hit can
reference a channel that no longer exists (`channels.list` → `items: []`). Every resolution therefore
ends with an authoritative verification via signal-service's `/channels/{id}/verify` primitive
(`FOUND` / `CHANNEL_NOT_FOUND`; a network/provider failure is surfaced as an error, never NOT_FOUND):

- deterministic leader **verified FOUND** → RESOLVED;
- deterministic leader **CHANNEL_NOT_FOUND** → rejection recorded, and the next ranked candidate is
  considered — but it must independently satisfy the same thresholds/ambiguity policy, then verify too;
- **no candidate both satisfies the policy and verifies** → AMBIGUOUS / UNRESOLVED;
- **verification unavailable (transient)** → not resolved, nothing invalidated.

### `last_verified_at` semantics

`last_verified_at` means **the identity was successfully verified against the provider at that time** —
nothing else. A search-only or unverified candidate has `last_verified_at = NULL`; it is stamped only
after a successful `channels.list` verification (at resolution and on each successful refresh), and a
failed/transient verification never updates it. Bounded verification provenance
(`candidate_score`, `candidate_signals`, `verification_method = channels.list`, `verified_provider_id`,
`verified_at`) is retained on the resolved identity; rejected stale candidates retain their
`provider_id` + score/signals + `verification_result = CHANNEL_NOT_FOUND`.

### Refresh + invalidation

Refresh verifies the channel id before writing anything. If `channels.list` returns `CHANNEL_NOT_FOUND`
the refresh writes **no** observations (never fabricated zeros), marks the identity `UNRESOLVED` with
`invalidation_reason = PROVIDER_ID_NOT_FOUND`, and — because the scheduler only enqueues RESOLVED
identities — it leaves normal recurring refresh until re-resolved. A transient/network failure
(`VERIFICATION_UNAVAILABLE`) is retried and never invalidates.

## Identity resolution

`canonical artist → bounded search → ranked candidates → deterministic evidence → authoritative
channels.list verification → RESOLVED / AMBIGUOUS / UNRESOLVED`. Transparent additive scoring:

| signal | weight |
|---|---|
| exact normalized name match | +0.5 |
| partial name match | +0.25 |
| music-topic / official signal | +0.3 |
| known handle match (source alias) | +0.4 |
| known URL match | +0.4 |
| explicit channel id (operator hint) | +1.0 |

Decision: RESOLVED if top ≥ 0.70 **and** beats the runner-up by ≥ 0.20; UNRESOLVED if top < 0.40;
otherwise AMBIGUOUS. **Name equality alone (0.5) never resolves** — corroboration is required. In live
testing, "Arijit Singh" against the real API returned AMBIGUOUS (multiple same-named channels, no
corroborating signal); supplying the discovered channel id as evidence resolved it at score 1.0.
Provider resolution never creates a canonical artist; ambiguous/unresolved states are recorded on an
auditable per-artist pending slot.

## Persisted metrics

`YOUTUBE_CHANNEL_VIEWS`, `YOUTUBE_SUBSCRIBERS`, `YOUTUBE_VIDEO_COUNT`, and per recent video
`YOUTUBE_VIDEO_VIEWS` / `_LIKES` / `_COMMENTS` (scope `CONTENT`, keyed by `video_id`, with `published_at`
preserved as `provider_timestamp`). `channel_id` and `video_id` are preserved throughout.

**Rounded subscribers.** YouTube publicly rounds subscriber counts to 3 significant figures, so
`YOUTUBE_SUBSCRIBERS` is stored as `PROVIDER_REPORTED` with `provenance.precision = rounded_3sf` — never
treated as exact. Views and video counts are `DIRECT_PROVIDER_VALUE`.

## Quota strategy

Search (1 unit, independent Search-Queries quota) and general reads (1 unit, shared pool) are accounted
in separate buckets that never mix. The diagnostic snapshot reports `search_queries` (used_calls /
daily_quota_calls) and `general_pool` (used / usable / reserve) separately, plus a `reconciles` flag
asserting the general total equals exactly GENERAL_READ + VIDEO_STATS_BATCH (no SEARCH, no sub-buckets).
Discipline:

- **search is for identity discovery, not measurement** — bounded, infrequent, and refused once
  `YOUTUBE_MAX_SEARCHES_PER_DAY` (default 50, ≤ the 100/day provider Search quota) is spent (HTTP 429);
- **identity resolution escalates to authoritative verification** — the top-N plausible candidates are
  verified via `channels.list` (general pool) even when search-only scoring is ambiguous, so a
  disambiguating channel can resolve; equally-named verified channels still stay AMBIGUOUS by margin;
- **repeated collection uses known channel/video ids** via `channels.list` / `videos.list`, never search.

Live run accounting example: 2 searches (2 Search-Queries units) + 6 general reads (8 pool units).

## Refresh

Resolved artists get a channel snapshot (default daily) and a bounded window of recent videos
(`YOUTUBE_RECENT_VIDEO_LIMIT`, default 5). Refresh is idempotent per day (re-running creates no
duplicates), restart-safe (state in Postgres), quota-aware, and failure-isolated (one artist's failure
never stops the others). See the [refresh scheduler](../demand-intelligence.md#refresh-scheduler).

## Derived read models

`subscriber_delta_7d/30d`, `channel_view_delta_7d/30d` + per-day velocities, `recent_video_view_velocity`,
`recent_upload_count_30d`, `uploads_per_week`, `recent_video_engagement_ratio`. Sparse history returns
`INSUFFICIENT_HISTORY` — a single snapshot honestly yields insufficient deltas, and nothing is
extrapolated. None of these is called popularity, market value, ticket demand, or booking potential.

## Configuration

`NQUARK_YOUTUBE_ENABLED`, `NQUARK_YOUTUBE_SEARCH_ENABLED`, `NQUARK_YOUTUBE_MAX_SEARCHES_PER_DAY`,
`NQUARK_YOUTUBE_CHANNEL_REFRESH_INTERVAL_SECONDS`, `NQUARK_YOUTUBE_RECENT_VIDEO_LIMIT`. The API key is
`NQUARK_YOUTUBE_API_KEY` **in signal-service**.

## Phase 5A.3 additions

- **Discovery surface**: bounded `search.list` queries (config `YOUTUBE_DISCOVERY_QUERIES`, India-market
  defaults) produce `artist_candidate` rows — never canonical artists. SEARCH bucket, quota-guarded.
- **Batch statistics**: `POST /v1/signals/youtube/videos/batch` (`videos.list`, ~1 unit / 50 ids) for
  quota-efficient known-video refreshes; the recent-videos path remains the fallback. All calls stay in
  signal-service; no scraping.
- **Quota buckets**: SEARCH / GENERAL_READ / VIDEO_STATS_BATCH, configurable fractions of the daily pool;
  quota day in the provider reset tz (`YOUTUBE_QUOTA_RESET_TZ`); target-utilisation + reserve enforced.
- **Catalogue backfill + video registry** (`youtube_video`): one-time bounded uploads discovery
  (`YOUTUBE_CATALOGUE_BACKFILL_DEPTH`), metadata captured once, observations time-series kept separate.
- **Hourly observations** for live metrics (`YOUTUBE_HOURLY_OBSERVATIONS`); daily records stay valid.
- **Identity verification integrity (5A.1a) unchanged**: search discovers, `channels.list` verifies,
  `last_verified_at` only on real verification, only authoritative NOT_FOUND invalidates. Thresholds are
  never lowered to raise coverage.
