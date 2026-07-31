# Deploying n-quark on Fly.io (dev-phase feed subset)

This deploys the slice crawl-space needs — the events feed and the pipeline that fills it —
consolidated onto **one Postgres** (no Neo4j/Qdrant/MinIO). It's the same platform the full
prod build grows into, so nothing here is throwaway.

## What gets deployed

| App (globally-unique name) | Public? | Purpose | Postgres? |
|---|---|---|---|
| `nquark-api-gateway` | **yes** (crawl-space hits this) | CORS + proxy; exposes `/v1/events` | no |
| `nquark-graph-service` | private (flycast) | graph store + `/v1/events` feed | **yes** |
| `nquark-observation-service` | private | append-only observations | **yes** |
| `nquark-entity-service` | private | canonical entities + alias folding | **yes** |
| `nquark-signal-service` | private | adapters + ingestion pipeline | no |
| `nquark-analytics-service` | private | demand/supply scoring (optional) | no |
| `nquark-ingest-cron` | private, scheduled | daily discover+ingest job | no |
| Fly Postgres cluster | private | the one datastore (shared, per-service version tables) | — |

`primary_region = "sin"` (Singapore) in every `fly.toml` — the nearest Fly region to India.
Every app service uses `auto_stop_machines = "stop"` + `min_machines_running = 0`, so idle
services scale to zero and the Fly proxy wakes them on the first request (via `.flycast`).

> **App names are globally unique across all Fly users.** If a name is taken, rename it in that
> service's `fly.toml` **and** update the matching `*.flycast` URL everywhere it appears
> (gateway + signal-service `[env]`, and the cron target).

## 0. Prereqs

```bash
# install flyctl: https://fly.io/docs/flyctl/install/
fly auth login
fly auth whoami   # confirm the target org
```

## 1. Postgres (the one datastore)

```bash
fly postgres create --name nquark-db --region sin --vm-size shared-cpu-1x --volume-size 3
# Attaching creates a database + role and prints a connection string (postgres://...):
fly postgres attach nquark-db --app nquark-graph-service
```

Take the printed `postgres://USER:PASS@nquark-db.flycast:5432/DBNAME` and **rewrite the scheme**
to the driver SQLAlchemy uses. All three DB-backed services share this one database (they own
distinct `alembic_version_*` tables, so their migrations never collide):

```bash
PG="postgresql+psycopg://USER:PASS@nquark-db.flycast:5432/DBNAME"   # note the +psycopg
for app in nquark-graph-service nquark-observation-service nquark-entity-service; do
  fly secrets set --app "$app" NQUARK_POSTGRES_URL="$PG"
done
```

> Newer alternative: **Managed Postgres** (`fly mpg create`). Same idea — grab its connection
> string, rewrite the scheme to `postgresql+psycopg://`, set it as `NQUARK_POSTGRES_URL`.
> pgvector isn't needed for the feed; it becomes relevant when feature-service lands.

## 2. Create the apps

```bash
for app in nquark-api-gateway nquark-graph-service nquark-observation-service \
           nquark-entity-service nquark-signal-service nquark-analytics-service; do
  fly apps create "$app" --org personal   # swap in your org slug
done
```

## 3. Deploy each service

Each service's `fly.toml` lives in its own directory; deploy from there so the build context
matches Docker Compose. Deploy the DB-backed ones first (they run `alembic upgrade head` at boot):

```bash
cd services/observation-service && fly deploy && cd ../..
cd services/entity-service       && fly deploy && cd ../..
cd services/graph-service        && fly deploy && cd ../..   # graph_backend=postgres -> migrates
cd services/signal-service       && fly deploy && cd ../..
cd services/analytics-service    && fly deploy && cd ../..
cd services/api-gateway          && fly deploy && cd ../..
```

## 4. Networking

Only the gateway is public. Internal services get a **private** IPv6 so `.flycast` resolves and
the Fly proxy can wake stopped machines.

```bash
# public entry (gateway):
fly ips allocate-v4 --shared --app nquark-api-gateway
fly ips allocate-v6 --app nquark-api-gateway

# private (flycast) for every internal app:
for app in nquark-graph-service nquark-observation-service nquark-entity-service \
           nquark-signal-service nquark-analytics-service; do
  fly ips allocate-v6 --private --app "$app"
done
```

## 5. Scheduled ingest (daily)

Runs the discover+ingest walk against signal-service, then exits. Idempotent — a daily run just
refreshes the catalog.

```bash
fly apps create nquark-ingest-cron --org personal
cd deploy/fly/ingest-cron
fly machine run . \
  --app nquark-ingest-cron \
  --region sin \
  --schedule daily \
  --vm-memory 256 \
  --env NQUARK_INGEST_TARGET=http://nquark-signal-service.flycast
cd ../../..
```

Run it once on demand to seed the catalog immediately:

```bash
fly machine run . --app nquark-ingest-cron --region sin --rm \
  --env NQUARK_INGEST_TARGET=http://nquark-signal-service.flycast
```

To ingest **real** platforms instead of the mock, set the provider + keys as secrets on
signal-service (never commit keys), e.g. `fly secrets set --app nquark-signal-service
NQUARK_TICKETING_PROVIDER=boshow NQUARK_SERPAPI_KEY=…`, then redeploy signal-service.

## 6. Smoke test

```bash
GW="https://nquark-api-gateway.fly.dev"
curl -s "$GW/health"
curl -s "$GW/v1/events?limit=5" | python3 -m json.tool | head -40
```

You should see the feed assembled off Postgres — canonical `venue_id`/`region_id`/`artist_ids`,
`redistribution_tier`, and an `updated_at` cursor.

## 7. Hand off to crawl-space

Give crawl-space the gateway base URL — its sync points at `{GW}/v1/events` (see
[`docs/events-feed.md`](../../docs/events-feed.md) and [`tools/crawl_space_sync.py`](../../tools/crawl_space_sync.py)).
The redistribution policy is enforced server-side, so crawl-space just consumes what it's handed.

## Cost (dev-phase, pay-as-you-go, iad/sin shared-cpu-1x)

- Fly Postgres (shared-cpu-1x, 512 MB, 3 GB volume): **~$3–6/mo** — the only always-on floor.
- App services: auto-stop to zero when idle; a light dev workload runs a few machine-hours/day
  → **~$1–4/mo** total across all of them.
- Scheduled ingest: seconds/day → negligible.
- **≈ $5–10/mo** during dev. No Neo4j/Qdrant/MinIO lines. Cost grows per machine as
  crawl/feature/media/intelligence services land — same platform, same Postgres.
