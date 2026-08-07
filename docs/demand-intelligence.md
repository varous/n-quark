# Demand Intelligence (Phase 5A)

n-quark's first **demand-side** layer, owned by
[artist-intelligence-service](../services/artist-intelligence-service/README.md). It observes public
demand for **canonical artists** on YouTube and Google Trends and juxtaposes it with observed live
**supply** (events), meeting only through `canonical_artist_id`.

This document covers the cross-cutting semantics; provider specifics are in
[providers/youtube.md](providers/youtube.md) and [providers/google-trends.md](providers/google-trends.md).

## Two evidence systems

| | Event supply | Public demand |
|---|---|---|
| sources | Boshow, District | YouTube, Google Trends |
| store | event Shadow Ledger (graph-service) | demand ledger (this service) |
| unit | observed commercial state of an event | platform demand facts about an artist |
| owner of identity | entity/graph (events + entities) | entity/graph (artists) — demand only *attaches* |

They are never merged. Demand metrics are **never** written to the event Shadow Ledger; the demand ledger
**never** stores ticketing/event state. No composite score fuses them — supply and demand are juxtaposed.

## Metric definitions

YouTube (persisted per snapshot; history preserved):

| metric | meaning |
|---|---|
| `YOUTUBE_CHANNEL_VIEWS` | lifetime channel view count |
| `YOUTUBE_SUBSCRIBERS` | subscriber count (**publicly rounded** — see epistemic statuses) |
| `YOUTUBE_VIDEO_COUNT` | number of public videos |
| `YOUTUBE_VIDEO_VIEWS` / `_LIKES` / `_COMMENTS` | per recent video (scope `CONTENT`, keyed by video id) |

Google Trends:

| metric | meaning |
|---|---|
| `GOOGLE_SEARCH_INTEREST` | **relative** search interest, 0–100 within a single pull — never absolute volume |

Derived read models (deterministic; never called popularity / market value / ticket demand / booking):
`subscriber_delta_7d/30d`, `channel_view_delta_7d/30d` and per-day velocities, `recent_video_view_velocity`,
`recent_upload_count_30d`, `uploads_per_week`, `recent_video_engagement_ratio`, `google_search_interest_change`.
Sparse history returns `INSUFFICIENT_HISTORY` — nothing is extrapolated.

## Epistemic statuses (`evidence_status`)

| status | when |
|---|---|
| `DIRECT_PROVIDER_VALUE` | an exact provider value (channel views, video count, video stats) |
| `PROVIDER_REPORTED` | a value the provider rounds/reports imprecisely (YouTube subscriber counts → `provenance.precision = rounded_3sf`) |
| `PROVIDER_SAMPLED` / `PROVIDER_NORMALIZED` | provider sampled/normalized the value (not a raw fact) |
| `IMPORTED_PROVIDER_EXPORT` | ingested from a Google Trends CSV export (distinguishable from OFFICIAL_API) |
| `DERIVED` | computed by a read model, not observed |
| `SEARCH_TERM_BASED` / `TOPIC_BASED` | (OFFICIAL_API Trends) the identity basis that produced the value |
| `UNKNOWN` | unspecified |

Provider normalization/sampling is **never** collapsed into "observed raw fact".

## Normalization constraints (Google Trends)

Trends 0–100 is relative **within one pull**. Every observation preserves its normalization context
(`normalization`, `comparison_window`, `time_range`, `geo`, `provider_mode`) and a `scale_note`.
Independently normalized exports are **never** compared as if they shared a scale — they carry distinct
export fingerprints and are grouped by normalization context. `SEARCH_TERM` and `TOPIC` histories are
kept distinct (never silently combined). See [providers/google-trends.md](providers/google-trends.md).

## Geographic demand

Geography is first-class: `artist × geography × metric × time`. Region scope ids use ISO 3166-2:IN codes
(e.g. `IN-WB`) with the provider's exact label preserved (`West Bengal`). The provider's granularity is
preserved exactly — no city-level precision is invented from state/subregion data. The supply-side join
keys regions by normalized label so Trends `West Bengal` aligns with graph `region:west-bengal`.

## Identity resolution

`canonical artist → bounded YouTube search → ranked candidates → deterministic evidence → RESOLVED /
AMBIGUOUS / UNRESOLVED`. Name equality alone never resolves; a topic/official, known-handle, known-URL,
or explicit-channel-id signal is required to clear the threshold. Ambiguous artists stay unresolved and
auditable. Provider resolution never creates a canonical artist.

## Data freshness

Every read model reports freshness: `last_observed_at`, `age_hours`, and a `stale` flag
(`> demand_freshness_stale_hours`, default 48h). Coverage diagnostics report stale artists, per-provider
history depth (1+/7d/30d), and provider failures.

## Refresh scheduler

A persisted `demand_refresh_job` queue (lease lock, retry classification, idempotency, per-provider
cadence, quota-awareness, bounded concurrency) reusing crawl-service's pattern. Repeated observation uses
**known channel/video ids** (channels.list / videos.list), never search. State lives in Postgres, so a
restart resumes idempotently. One artist/provider failure is isolated from the rest.

## Known limitations

- YouTube subscriber counts are publicly rounded (3 sig figs) → `PROVIDER_REPORTED`, not exact.
- Google Trends OFFICIAL_API needs alpha access; until then Trends is **import-only** (labeled CSV
  exports). No unofficial scraping is part of the demand path. See the provider doc for the manual
  alpha-access prerequisite.
- Coverage is **observed public demand for a bounded pilot cohort — not complete market demand coverage**.
- Momentum deltas need ≥2 snapshots across days; a single snapshot honestly reports `INSUFFICIENT_HISTORY`.
- The supply/demand geography join relies on region-label alignment between Trends and the graph.
