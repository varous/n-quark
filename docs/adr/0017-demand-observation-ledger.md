# ADR 0017 — A separate demand-observation ledger (Phase 5A)

- Status: Accepted
- Date: 2026-08-07
- Phase: 5A — Public Demand Intelligence Foundation
- Relates to: [docs/demand-intelligence.md](../demand-intelligence.md),
  [artist-intelligence-service README](../../services/artist-intelligence-service/README.md),
  ADR-0001 (Shadow Ledger), ADR-0016 (Fly continuous collection)

## Context

n-quark has one evidence system so far: **event supply** (Boshow/District → event observations → the
event Shadow Ledger in graph-service). Phase 5A adds **public demand** (YouTube, Google Trends → artist
demand observations). The two are different in kind — different provenance, different epistemics (a
relative 0–100 Trends index is not an observed commercial fact), different cadence — and must not be
conflated. They meet only through `canonical_artist_id`.

signal-service already acquires YouTube and Trends signals, but it is **stateless** and part of the live
crawl→signal collection spine. Two questions needed durable answers: where do demand observations live,
and where does the demand-refresh machinery run.

## Decisions

1. **A dedicated demand ledger, separate from the event Shadow Ledger.** A new service,
   `artist-intelligence-service`, owns four tables — `artist_external_identity`,
   `artist_demand_observation`, `provider_quota_day`, `demand_refresh_job` — in its own Alembic version
   table (`alembic_version_artist_intel`). YouTube/Trends metrics are **never** written into the event
   Shadow Ledger, and the demand ledger never stores ticketing/event state. Observations are append-only
   and idempotent on an `observation_key`; history is preserved, never overwritten as current-state.

2. **Reuse signal-service for acquisition — no parallel ingestion path.** All YouTube HTTP calls, the API
   key, and mock modes stay in signal-service (extended with two additive, acquisition-only primitives:
   channel *search* for identity discovery and recent *videos*). The demand service's YouTube "provider"
   is a thin client that calls signal-service, exactly as analytics-service calls crawl/graph. There is
   no second YouTube client anywhere.

3. **A separate service, to protect the collection spine.** Demand persistence + the refresh scheduler
   live in `artist-intelligence-service`, not inside signal-service, so a demand-layer failure (or its
   migrations/scheduler) can never disrupt the running crawl→signal event collection (Phase 4D §22).
   The refresh queue reuses crawl-service's proven lease/retry/idempotency pattern rather than inventing
   a new scheduling architecture.

4. **Canonical identity stays owned by entity/graph.** Provider resolution attaches an external identity
   to an *existing* canonical artist and never creates one. Name-equality alone never resolves; ambiguous
   identities stay `AMBIGUOUS`/`UNRESOLVED` and auditable.

5. **Provider-neutral, epistemically honest observations.** A provider contract exposes capability flags
   (YouTube: search/metadata/snapshots; Trends: geographic/historical/import). Every observation records
   an `evidence_status` (e.g. `DIRECT_PROVIDER_VALUE`, `PROVIDER_REPORTED` for rounded subscriber counts,
   `IMPORTED_PROVIDER_EXPORT` for Trends CSVs). Google Trends is modeled as **relative** search interest
   (0–100 within a pull), never absolute volume; independently normalized exports are never compared as
   if they shared a scale. No composite popularity/value/booking score is computed; supply and demand are
   only juxtaposed. Temporal co-movement is reported without any causal claim.

6. **Google Trends: OFFICIAL_API (gated) + IMPORT, no scraping.** The official provider is used only with
   valid alpha credentials + endpoint; otherwise it reports `ACCESS_UNAVAILABLE` and the interim path is
   structured import of legitimately-obtained CSV exports. No unofficial scraping is part of the Phase 5A
   demand path. (signal-service's pre-existing proxy-based Trends remains for the legacy graph pipeline
   and is deliberately not the production demand path.)

## Consequences

- Two clean evidence systems that meet only at `canonical_artist_id`; demand epistemics never contaminate
  the event Shadow Ledger.
- The demand service is deployable privately and independently on Fly (opt-in, not in the deploy script),
  so it cannot affect the crawl collection spine.
- Trends fidelity is import-limited until Google Trends API alpha access is granted; the official provider
  is already wired to switch on behind the same contract without touching read models.
- All flags default off; enabling the service runs its migrations and (optionally) an in-process refresh
  loop that is restart-safe via Postgres.
