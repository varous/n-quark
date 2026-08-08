# ADR-0018 — Indian artist universe & demand saturation (Phase 5A.3)

## Status
Accepted (2026-08-09).

## Context
Phase 5A tracked demand only for artists that already existed as canonical entities derived from the
ticketing sources (Boshow/District). The effective dependency was:

```
observed event → canonical artist → YouTube identity → demand observations
```

So an artist absent from the two ticketing sources could never enter demand tracking, and BookMyShow's
absence (partner-walled, never scraped) risked becoming a silent gatekeeper for the whole artist universe.
Temporal demand data is also irrecoverable — a subscriber/view/interest value not captured at hour *t*
cannot be reconstructed later — so under-collecting quota is a permanent loss.

## Decision
Decouple the artist universe from ticketing coverage and maximise irreplaceable temporal collection,
**reusing** the existing architecture (signal-service = the single external acquisition path;
artist-intelligence-service = stateful demand; entity/graph = canonical ownership) rather than adding a
new crawler or provider service.

1. **Candidate layer** (`artist_candidate`). Discovery from multiple surfaces produces *candidates*, never
   canonical artists. Idempotent on `(discovery_source, discovery_source_id)`. A candidate becomes linked
   to a canonical artist only through the existing entity semantics; arbitrary YouTube results never
   pollute the canonical graph.
2. **Automatic onboarding + backfill.** A canonical artist without a YouTube identity is enqueued for
   identity discovery automatically (event-derived onboarding + a bounded periodic backfill of the whole
   existing cohort) — no manual operator invocation. Work is persisted and quota-managed.
3. **Independent YouTube discovery.** Bounded, configurable, India-market-oriented official Data API
   searches produce candidates. The query set is version-controlled config, not a hardcoded list.
4. **India market-presence evidence** (`artist_market_evidence`) as explicit, provenance-bearing
   classifications — `CONFIRMED_LIVE_INDIA` / `INDIA_DEMAND_OBSERVED` / `INDIA_MARKET_CANDIDATE` — never a
   single opaque "India relevance score". "Discovered on YouTube" ≠ "performs in India".
5. **Granular quota buckets** (`provider_quota_bucket_day`): SEARCH / GENERAL_READ / VIDEO_STATS_BATCH,
   each a configurable fraction of the daily pool, with the quota day following the provider's real reset
   timezone (midnight Pacific), not UTC. Target ~95 % utilisation with a 5 % reserve; the scheduler
   **defers** (never invalidates) when the reserve is reached. Search is spent on discovery/resolution,
   never on known-id refresh.
6. **Video registry** (`youtube_video`) separate from time-series observations: stable metadata captured
   once via a bounded one-time catalogue backfill; demand metrics collected separately.
7. **Hourly temporal resolution** for YouTube live metrics (Trends stays daily), idempotent on the
   observation hour. Old daily records stay valid — hourly precision is never retrofitted onto them.
8. **Adaptive + event-aware cadence** (config-driven, deterministic): fresh videos and imminent-Indian-
   event artists earn higher resolution; long-tail content is sampled sparsely.
9. **Acquisition priority** classes (P0–P4) order the queue by operational urgency — not artist value.
10. **Batch statistics primitive** (`videos.list`, 1 unit / 50 ids) in signal-service for quota-efficient
    known-video refreshes, with the existing per-video path as fallback. All YouTube calls stay in
    signal-service; no unofficial scraping.
11. **Google Trends** unchanged in policy (official-API-or-import, no scraping). The official provider
    gains gated backfill/incremental method shapes for when alpha access is granted; there is no intraday
    Trends polling.

## Consequences
- Artist intelligence no longer depends solely on ticketing coverage; BMS is one future *supply* source,
  not the gatekeeper.
- 5A.1a identity-verification integrity is preserved: search discovers, `channels.list` verifies,
  `last_verified_at` is set only on real verification, and only authoritative NOT_FOUND invalidates.
- More data is preferred over false identity matches: thresholds are never lowered to raise coverage.
- Migration `002_artist_universe` is additive + reversible; no historical demand row is rewritten.
- The admin surface stays read-only and local-only; no new public Fly app is introduced.
