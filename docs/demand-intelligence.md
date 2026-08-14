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

`canonical artist → bounded YouTube search (DISCOVERY) → ranked candidates → AUTHORITATIVE channels.list
verification → deterministic evidence → RESOLVED / AMBIGUOUS / UNRESOLVED`. Name equality alone never
resolves; a topic/official, known-handle, known-URL, or explicit-channel-id signal is required to clear
the threshold. Ambiguous artists stay unresolved and auditable. Provider resolution never creates a
canonical artist.

**Discovery vs verification (5B.2.7).** Search is candidate discovery; provider verification is a separate
step. When search-only scoring is AMBIGUOUS, the top-N *plausible* candidates are verified via
`channels.list` (not only pre-declared clear leaders), and the decision is re-made on the authoritative
metadata (a verified exact channel-title match earns a bounded bonus). This lets a disambiguating channel
resolve, while equally-named verified channels still stay AMBIGUOUS by the clear-leader margin — thresholds
are never lowered to force resolution. A **candidate identity row is NOT a verified provider channel**: the
funnel (`youtube_pipeline`) reports eligible artists → identity candidates → verified channels → needs
review → unresolved → owned videos registered, and the UI labels *verified channels* distinctly from
*identity candidates*.

**Non-RESOLVED identities stay schedulable (5B.2.7).** A successful HTTP identity job is not terminal for
the identity: AMBIGUOUS/UNRESOLVED artists are re-enqueued on a status-based cadence (UNRESOLVED → backoff
retry; AMBIGUOUS → slower re-resolution). Re-enqueue eligibility is gated on the crawl product registry, so
invalid/orphan/compound/quarantined canonicals never enter active YouTube monitoring (and it fails closed
if the registry is unavailable). A verified channel automatically earns an owned-uploads catalogue backfill
(official uploads playlist, `relationship_type=OWNED_CONTENT`, never search) + recurring channel/video/
**catalogue** refresh.

**Acquisition pipeline diagnostics (5B.2.8).** `/v1/internal/demand/youtube-pipeline` exposes the whole
funnel in product terms — identity (candidates / verified channels / needs review / unresolved / quota
deferred), owned content (channels with catalogue / owned videos / videos observed / videos with ≥2
snapshots / videos with sufficient movement history), scheduler next-jobs, and **stuck-state** detectors
for impossible states (resolved-without-catalogue, verified-no-videos-after-catalogue,
owned-videos-without-stats-job, stats-succeeded-no-observations, nonresolved-never-scheduled). These are
diagnostics only; nothing is auto-repaired.

**Snapshot semantics (5B.2.8).** views / likes / comments recorded at one collection time are **3 metric
observations = 1 temporal snapshot**. The diagnostics report metric-observation count and temporal-snapshot
count **separately** (videos with 1 / 2+ / 3+ distinct snapshot timestamps). Velocity needs ≥2 snapshots;
movement classification (NORMAL / RISING / BREAKOUT_CANDIDATE / COOLING) needs
`movement_min_observations` (3) — with fewer, the honest state is INSUFFICIENT_HISTORY (velocity may still
be available). Movement thresholds are unchanged; nothing is forced to a flashy state.

**Search pacing (5B.2.8).** The identity backlog is paced: bounded jobs per scheduler tick, quota-aware
deferral (deferrals reschedule and never burn the retry budget), deterministic per-artist priority
(upcoming-event / multi-reference artists outrank legacy candidates), and a protected Search-Queries
reserve for high-priority intake — so a large backlog can never consume the whole daily search allowance
in one burst and new Watchlist artists are never starved.

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

## Phase 5A.3.1 — candidate promotion & acquisition closure

Closes the four 5A.3 gaps without redesign. See ADR-0018.

**Candidate → canonical promotion** (`promotion.py`). Deterministic, auditable, and canonical ownership
stays OUTSIDE the demand service:
- `MATCH_EXISTING_CANONICAL` — the candidate's normalized name matches an existing canonical ARTIST
  (crawl `/entity-resolution/entities`) → **link** (no create);
- `MULTI_SOURCE_CONFIRMED` — the same artist seen from ≥`CANDIDATE_PROMOTION_MIN_SOURCES` independent
  discovery sources → **create via the crawl owner** (`POST /v1/internal/governance/create-artist`, which
  writes the `artist:<slug>` graph node in crawl/graph);
- `INDIA_LIVE_EVIDENCE_PLUS_MUSIC_IDENTITY` — an EVENT-sourced sibling (India live) + a music identity
  signal → create via the crawl owner.
A single YouTube search hit satisfies none of these, so weak evidence never canonicalises; insufficient
candidates stay `RESOLUTION_PENDING`. After link/create, demand identity resolution is enqueued
automatically (idempotent). The demand service **never writes a canonical id to its own DB**. Backlog is
drained in bounded persisted passes (`candidate_promotion_batch_size`) by the collector +
`POST /v1/internal/candidates/promote`.

**Bounded YouTube ecosystem discovery** (`YOUTUBE_ECOSYSTEM`). Configured seed channels
(`YOUTUBE_ECOSYSTEM_SEED_CHANNELS`: festival/promoter/venue/media/label) → their recent uploads (official
API, one hop, no recursive crawl) → `artist_candidate` evidence only. Bounded by `…MAX_SEEDS_PER_RUN` /
`…MAX_VIDEOS_PER_SEED` / `…MAX_CANDIDATES_PER_RUN`.

**Dynamic search-quota allocation.** Search spend is sub-accounted by purpose
(`SEARCH:unresolved|discovery|ambiguity` rows in `provider_quota_bucket_day`). A purpose may always use its
configured slice; it may **borrow** unused allocation only when other purposes have no pending work, or the
provider day is **near reset** (`near_provider_reset`) so otherwise-idle budget is used before it is lost.
The global reserve is never borrowable. Diagnostics expose configured fraction, used, remaining, borrowed
per purpose (`/demand/quota-buckets` → `search_allocation`).

**Live event-proximity cadence** (`EVENT_AWARE_CADENCE_ENABLED`). The scheduler reads the nearest upcoming
Indian event for a canonical artist LIVE from the graph (FEATURES neighbours), bounded + best-effort, and
feeds the distance into the existing cadence bands (min with the base cadence → event proximity always
increases cadence). Event data is never duplicated into the demand service; a graph failure degrades safely
to normal cadence.

Also fixed a latent 5A.3 defect: `crawl_client.artists` targeted `/v1/internal/entities` (404) instead of
`/v1/internal/entity-resolution/entities`, so backfill/coverage silently saw zero canonical artists.

## Phase 5A.3.2 — canonical artist state reconciliation

**Ownership model (documented).** Canonical ARTIST **identity** is owned by crawl's entity-resolution
registry (`EntityResolutionCandidate`, enumerated at `/v1/internal/entity-resolution/entities`); the
graph is the **representation** (artist nodes + FEATURES/IDENTIFIES edges) written by the resolver
(`_write_graph`) and by governance `create-artist`. artist-intelligence never owns canonical identity —
it reads the registry (`crawl_client.artists`) and keys demand on `canonical_artist_id`.

**Root cause of the prod "0 artists".** Production had genuinely not accrued canonical artists yet
(1 tracked event, empty graph, empty registry) — not registry drift and not a hidden graph cohort. The
lone prod `artist:arijit-singh` demand rows are an **orphan** from earlier manual validation (a
canonical_artist_id supplied directly, never backed by a registry/graph node).

**Reconciliation.** `create-artist` (governance) now also writes a RESOLVED registry row so
externally-created (promotion) artists appear in the authoritative `/entities` enumeration — closing a
5A.3.1 gap where such artists were graph-only and invisible to backfill. `POST
/v1/internal/governance/reconcile-graph-artists` idempotently registers any pre-existing graph-only
ARTIST node into the registry (bounded; never creates/duplicates nodes; never rewrites ids).

**Diagnostics + orphan audit.** `/demand/artist-universe` gains a `canonical_reconciliation` block:
canonical-registry artists, graph ARTIST nodes, artists referenced by demand identities/observations,
confirmed-live-India count, and **orphan demand references** (demand `canonical_artist_id` values absent
from the canonical enumeration) — reported for audit, never silently rewritten. Degrades safely when
crawl/graph are unavailable.

Also fixed: the graph-only existence check tolerates multiple candidate rows per canonical id; graph node
listing respects the service's 500 cap.
