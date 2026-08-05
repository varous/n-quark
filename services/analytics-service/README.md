# analytics-service

Deterministic read models over the canonical entity graph and Shadow Ledger. **No prediction, no
scores, no total-market claims** — it reports *observed supply* and *observation quality* from what
n-quark has captured (Boshow, District).

## Two surfaces

| Surface | Prefix | Status |
|---|---|---|
| **Market read models** (Phase 4A) | `/v1/analytics/market/...` | current |
| Legacy demand/region scoring | `/v1/analytics/artists/{id}`, `/v1/analytics/regions/{id}` | retained for compatibility, not extended |

The market read models are the deterministic core; the legacy scoring endpoints predate this phase and
are left untouched so existing callers keep working. New work goes under `/market`.

## Architecture

```
crawl-service  (capture coverage, canonical entities, resolved entities, governance/supersession)
graph-service  (event node props, IN_REGION, Shadow Ledger transitions)
        │  read-only
        ▼
datasource.py  → normalized snapshot (ObservedEvent[] + EntityMeta{} + Canonicalizer)
        ▼
readmodels.py  → pure deterministic aggregates      projection.py → canonical folding
        ▼
routes/market.py → bounded, paginated, trace-able endpoints
```

- **Canonical projection** (`projection.py`): folds a legacy/superseded id onto its canonical via
  `SUPERSEDED_BY` (then alias) edges, with cycle + invalid-chain protection. **Non-destructive** — it
  reads relationship maps only; it never migrates or deletes graph nodes. Every model counts by
  canonical id so a legacy naive-projection node and its evidence-resolved canonical are not
  double-counted. Legacy nodes remain fully queryable (they resolve to the canonical view).
- **Read models** (`readmodels.py`): pure functions over the snapshot — regional supply, artist / venue
  / organizer / series activity, observation quality, commercial state. No network here.
- **Datasource** (`datasource.py`): the only impure layer; bounded fan-out over the internal APIs.

## Endpoints

See [docs/analytics.md](../../docs/analytics.md) for the full contract. Summary:

```
GET /v1/analytics/market/canonicalize/{entity_id}
GET /v1/analytics/market/regions[/{region_id}]
GET /v1/analytics/market/artists[/{artist_id}]
GET /v1/analytics/market/venues[/{venue_id}]
GET /v1/analytics/market/organizers[/{organizer_id}]
GET /v1/analytics/market/series[/{series_id}]
GET /v1/analytics/market/observation-quality
GET /v1/analytics/market/commercial-state
```

Facets on every endpoint: `date_from`, `date_to`, `source`, `city`, `region`, `trace`. Lists add
`limit`/`offset` with a stable sort (most events desc, then canonical id). Every response carries a
`scope` block (observation scope + limitations); `trace=true` adds inclusion/exclusion, canonical
resolution paths, superseded dedup, missing-field exclusions and metric definitions.

## Run (dev)

```bash
# needs crawl-service (8001) + graph-service (8006) up with entity resolution enabled
docker compose up -d --no-deps analytics-service         # :8007
curl -s localhost:8007/v1/analytics/market/regions | jq
```

## Invariants

- Deterministic + explainable only; no LLM, no prediction, no demand/popularity/sell-through scores.
- Counts use canonical ids; legacy/superseded folded, never double-counted; unmerged duplicates
  without a `SUPERSEDED_BY` edge are **not** silently merged (reported, not guessed).
- Read-only; no graph migration; raw source + Shadow Ledger remain authoritative.
- Query-time read models (no materialized tables yet — see [ADR-0013](../../docs/adr/0013-canonical-analytics-projection.md)).
