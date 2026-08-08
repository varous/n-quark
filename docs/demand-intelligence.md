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

## Phase 5A.2 — inspection surface (read-only, local-only)

The Phase 5A read models are exposed through the existing local admin **inspection console** — no SQL,
curl, or Fly logs needed for routine inspection. This is an observability phase, not a new intelligence
model: no metric is computed in the gateway or the browser; the gateway BFF only fetches, bounds, and
normalises presentation, and analytics stay in artist-intelligence-service.

**Topology** (unchanged from Admin C): `React admin → api-gateway /admin/v1 → artist-intelligence-service`.
The browser never calls the demand service directly. A demand-service outage degrades the relevant panel
(`available: false`) and never breaks the rest of the console.

**Surfaces**
- **Demand Intelligence** screen (nav): coverage, YouTube provider health with a **REAL / MOCK / UNKNOWN**
  mode badge (MOCK is rendered as an unmissable alert; mode is never assumed REAL — UNKNOWN if
  signal-service health can't be read), today's quota counters, read-only scheduler state, and Google
  Trends OFFICIAL_API / IMPORT status.
- **Artist Demand** (`#/demand/artists/<canonical-artist-id>`, also embedded as a section on the ARTIST
  entity page): external identities with explicit verification (`RESOLVED` + provider-verified +
  `last_verified_at`; `UNRESOLVED`/`REJECTED` + reason), YouTube current state + deltas + recent-video
  context, independent momentum components, Google Trends (relative interest + regional distribution +
  normalization context), a sortable geography table, observed live supply, and bounded observation history.
- **Event → Demand context** tab: per-resolved-artist YouTube freshness + 7d/30d momentum + the
  event-relative co-movement timeline (T-60…T+7), labelled *temporal co-movement only — no causal inference*.
- **Dashboard**: a compact demand summary card.

**Read-only + epistemic display rules enforced in the UI**
- No mutation controls exist anywhere (no resolve / refresh / import / retry / scheduler actions).
- `confidence` is labelled the identity-resolution match strength — **not** popularity, reach, or quality.
- Subscriber counts carry the provider-reported/rounded caveat; rounded deltas are not shown as exact.
- `INSUFFICIENT_HISTORY` and Trends `ACCESS_UNAVAILABLE` are shown as legitimate evidence states in a
  neutral tone, never as errors.
- Google Trends values are labelled **relative search interest** (0–100 within a pull), never volume;
  independently normalised exports are never compared on one scale.
- Supply is labelled **observed live supply** (not total activity); underlying values are always shown and
  no composite demand×supply score is introduced in the frontend.

**Boundary**: the admin frontend and the admin BFF remain **local-only** — the frontend is in no Fly
manifest and `NQUARK_ADMIN_API_ENABLED`/`NQUARK_ADMIN_LOCAL_MODE` stay pinned off on cloud (enforced by
test). The production artist-intelligence-service remains private Flycast infrastructure; this phase adds
no cloud surface. `docs/product-spec.md` is untouched.

## Phase 5A.3 — Indian artist universe & demand saturation

Decouples the artist universe from ticketing coverage and maximises irreplaceable temporal collection.
See ADR-0018. Reuses the existing spine (signal-service = single acquisition path; artist-intelligence =
stateful demand; entity/graph = canonical ownership). All new behaviour is flag-gated and OFF by default.

**Candidate vs canonical artist.** `artist_candidate` is a *proposed* artist from a discovery surface
(EVENT / YOUTUBE_SEARCH / YOUTUBE_ECOSYSTEM / IMPORT), idempotent on `(discovery_source,
discovery_source_id)`. Statuses: NEW → RESOLUTION_PENDING → RESOLVED / AMBIGUOUS / REJECTED. A candidate
is **never** a canonical artist and never creates one; it links to an existing canonical artist through
the entity architecture. Arbitrary YouTube results therefore cannot pollute the canonical graph.

**Automatic onboarding + backfill.** `POST /v1/internal/artists/{id}/onboard` records a RESOLVED
candidate + India market evidence and enqueues identity discovery if no RESOLVED YouTube identity exists.
`POST /v1/internal/backfill/artists` enumerates the existing canonical cohort (via crawl) and queues those
lacking an identity — bounded per pass (`ARTIST_BACKFILL_BATCH_SIZE`), persisted, quota-managed. The
collector runs both automatically when `ARTIST_AUTO_ONBOARD_ENABLED` / `YOUTUBE_DISCOVERY_ENABLED` are on,
so no manual operator call is needed. Boshow/District historical artists thus enter the pipeline on their
own; BookMyShow is never a gatekeeper.

**India market-presence evidence** (`artist_market_evidence`) — provenance-bearing *classifications*, not
a score: `CONFIRMED_LIVE_INDIA` (observed event/lineup/venue/promoter/tour/feed), `INDIA_DEMAND_OBSERVED`
(India/sub-region Trends — not proof of performing), `INDIA_MARKET_CANDIDATE` (market-relevant, weaker
evidence). Idempotent on `(canonical_artist_id, evidence_class, source, source_ref)`.

**Quota model.** Per-bucket accounting (`provider_quota_bucket_day`): SEARCH / GENERAL_READ /
VIDEO_STATS_BATCH, each a configurable fraction of the daily pool. The quota day follows the provider's
reset timezone (`YOUTUBE_QUOTA_RESET_TZ`, default midnight Pacific), not UTC. Target utilisation
(`YOUTUBE_QUOTA_TARGET_UTILIZATION`, default 0.95) with a reserve (0.05); the scheduler **defers** work
(never invalidates identities) once the reserve is reached. Search is allocated across
unresolved-artist / new-discovery / ambiguity / reserve (`YOUTUBE_SEARCH_ALLOC_*`) and never spent on
known-id refresh. The legacy `provider_quota_day` aggregate is still written for back-compat.

**Video registry** (`youtube_video`) is separate from time-series observations: a bounded one-time
catalogue backfill (`YOUTUBE_CATALOGUE_BACKFILL_DEPTH`) captures stable metadata once (title,
published_at, …); demand metrics live in `artist_demand_observation`.

**Hourly observations.** YouTube live metrics bucket by hour (`YOUTUBE_HOURLY_OBSERVATIONS`, default on);
Trends stays daily. Idempotent on the observation hour — a same-hour rerun is one logical observation, the
next hour is new history. Old **daily** records remain valid; hourly precision is never retrofitted.

**Adaptive + event-aware cadence** (config-driven, deterministic): channel cadence by event proximity /
activity; video cadence by upload age; artist cadence accelerated around Indian events (T-60 → T+3). Values
are `CADENCE_*` config, bounded by quota. This enriches the event-response read model without changing its
epistemic claim (temporal co-movement only, no causal inference).

**Acquisition priority** (P0 upcoming event … P4 global candidate) orders the queue — operational urgency,
not artist value. **Failure semantics** are typed: only authoritative `PROVIDER_ID_NOT_FOUND` invalidates;
`QUOTA_EXHAUSTED` defers; transient failures retry with backoff (5A.1a preserved).

**Diagnostics.** `GET /v1/internal/demand/artist-universe` (candidate counts, India evidence classes,
identity coverage, video registry counts, discovery-source contribution, queue depth) and
`/demand/quota-buckets` (per-bucket used/budget/remaining + reserve), surfaced read-only through the
existing Demand Intelligence admin screen.
