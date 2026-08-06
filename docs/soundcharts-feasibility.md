# Soundcharts feasibility study (Phase 4C)

**Status: research only. No Soundcharts code, credentials, or production integration exist in this
phase. Soundcharts is NOT a ticketing source and must never implement the ticketing adapter contract.**

This report separates two distinct concerns that must not be conflated:

| Layer | What it is | How n-quark gets it today |
|---|---|---|
| **Ticketing-supply observation** | *What events exist, where, when, at what price/availability* | public ticketing adapters (Boshow, District, Skillbox) — deterministic, public_scrape |
| **Licensed artist intelligence** | *Who an artist is across platforms, their audience size/trend, touring history* | **not built** — would require a licensed provider such as Soundcharts under a paid API contract |

Soundcharts belongs entirely to the second layer. It answers "how big/rising is this artist" — it does
**not** tell us what tickets are on sale. Folding it into ticketing acquisition would be a category error.

## What Soundcharts is (from public product/marketing surface only)

Soundcharts is a music-market-intelligence API. Its public materials describe artist metadata,
cross-platform identifiers, audience/follower metrics and time series, playlist/chart/radio signals, and
(via partnerships) some live/touring data. **All endpoint shapes, field availability, quotas, and pricing
below require live credential verification — they are stated as expectations to test, not confirmed
facts.** This document does not fabricate endpoint availability.

## Capability assessment (to be confirmed with a key)

| Capability | Expected | Confidence without a key | Needs live verification |
|---|---|---|---|
| Artist search (name → candidates) | yes | medium | **yes** |
| Artist metadata (name, country, genres) | yes | medium | **yes** |
| Soundcharts artist UUID (stable id) | yes | medium | **yes** |
| Platform ids (Spotify / YouTube / Apple / etc.) | yes | medium | **yes** |
| Local (per-country/city) streaming audience | partial/plan-gated | **low** | **yes** |
| Audience & follower time series | yes (plan-gated depth) | low | **yes** |
| Artist event / touring data | uncertain — may be partner-gated | **low** | **yes** |
| Venue / ticket-price fields | **unlikely** (not a ticketing product) | low | **yes** |
| Playlist / radio / chart signals | yes | medium | **yes** |
| API quotas / plan tiers | unknown | **none** | **yes** |

**Cannot be confirmed without an API key:** exact endpoint paths and response schemas; whether local
(India / Kolkata-level) audience granularity exists on an affordable tier; time-series depth and history
window per plan; whether any touring/event data is included or is a separate partner feed; per-endpoint
quota costs and rate limits; monthly price for a pilot-sized plan; caching/storage/redistribution terms in
the licence.

## Recommended minimal proof-of-value request set (once a sandbox key exists)

Run against a **small labelled cohort** — the ~26 canonical artists n-quark already resolved from
Boshow/District/Skillbox (e.g. *Skinny Mos*, *Prateek Kuhad*, *Pilu*):

1. `search_artist(name)` for each cohort artist → capture UUID + match confidence (validate identity
   resolution quality against our canonical artists).
2. `resolve_platform_ids(uuid)` → Spotify/YouTube ids (feeds our cross-platform entity convergence).
3. `get_artist_metadata(uuid)` → genres/country (one call/artist).
4. `get_audience_history(uuid, platform=spotify, window=90d)` for ~5 artists only → gauge time-series
   depth + quota cost.
5. `get_local_audience(uuid, country=IN)` for the same ~5 → **the key India-relevance test**; if local
   granularity is absent or plan-gated beyond budget, the pilot value drops sharply.
6. One `get_touring_history(uuid)` probe for 2 artists → confirm whether any event data exists at all.

**Estimated request consumption for the pilot:** ~26 (search) + 26 (platform ids) + 26 (metadata) +
5 (audience history) + 5 (local audience) + 2 (touring) ≈ **90 calls**, plus retries — well inside any
trial quota, and enough to answer the go/no-go questions without committing to a plan.

## Suggested caching & refresh policy (if adopted later)

- **Static identity** (UUID, platform ids, genres): cache indefinitely; refresh only on a resolution miss.
- **Audience/follower time series**: licensed, slow-moving — refresh **weekly** at most; store only the
  derived series we're licensed to retain, never bulk-mirror the provider.
- **Chart/playlist signals**: refresh **daily→weekly** per artist priority.
- Respect the licence's storage/redistribution limits: keep artist-intelligence in a **separate store**
  from the public ticketing-supply observations, clearly provenance-tagged as licensed third-party data,
  and never expose it through the public events feed without checking redistribution terms.

## Provider-neutral contract proposal (future, not implemented)

Soundcharts would sit behind a provider-neutral interface — **not** the ticketing adapter — so it can be
swapped for another artist-intelligence vendor and so licensed data stays cleanly separated:

```python
class ArtistIntelligenceProvider(Protocol):
    async def search_artist(self, name: str) -> list[ArtistMatch]: ...
    async def resolve_platform_ids(self, provider_artist_id: str) -> dict[str, str]: ...
    async def get_artist_metadata(self, provider_artist_id: str) -> ArtistMetadata: ...
    async def get_audience_history(self, provider_artist_id: str, *, platform: str,
                                   window_days: int) -> list[AudiencePoint]: ...
    async def get_local_audience(self, provider_artist_id: str, *, country: str) -> LocalAudience: ...
    async def get_touring_history(self, provider_artist_id: str) -> list[TouringRecord]: ...
    async def get_chart_or_playlist_signals(self, provider_artist_id: str) -> list[ChartSignal]: ...
```

Design constraints for that future phase:
- It resolves onto the **existing canonical artist entities** (Phase 3.1) — an `ArtistIntelligenceProvider`
  enriches a canonical artist; it never creates ticketing events and never implements
  `TicketingAdapter`.
- Every value carries provenance (`licensed_provider`, retrieval time) and epistemic status; audience
  numbers are the provider's estimate, not ground truth.
- API keys are user-supplied via `.env` (never handled by the agent), and all Soundcharts behaviour would
  be feature-flagged off by default, like every external provider in this repo.

## Recommendation

Keep ticketing-supply observation (this phase) and artist intelligence strictly separate. Do **not** add
Soundcharts now. When artist intelligence becomes a priority, start with the ~90-call proof-of-value set
above against a sandbox key to confirm India-local audience granularity and pricing **before** committing
to the `ArtistIntelligenceProvider` implementation.
