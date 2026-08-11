# Deploying n-quark's collection spine on Fly.io (Phase 4D)

Continuous **private** collection: five services observe Boshow + District into one Postgres, always-on.
This is the observation spine only — **no public/customer surface is deployed**.

> **The admin dashboard is LOCAL-ONLY and is NOT deployed here** (see `docs/deployment.md`). Neither is
> `api-gateway`, `analytics-service`, `entity-service`, nor the frontend.
>
> **observation-service IS deployed** (Phase 5A.3.3): it is a HARD dependency of signal-service's ticketing
> `/ingest` (the capture write path). Without it, every capture ingest returns 502 and the collector
> silently records `SOURCE_UNAVAILABLE` — nothing is ever captured PRESENT, so no entities, no canonical
> artists, and an empty graph. `signal-service /health/ready` now surfaces this dependency explicitly (503
> when observation-service is unreachable). `entity-service` remains best-effort/undeployed (signal's
> entity resolution degrades gracefully; crawl owns canonical identity).

## Resource topology

| App (private, Flycast only) | Port | Role | Postgres |
|---|---|---|---|
| `nquark-graph-service` | 8006 | canonical graph + Shadow Ledger | **yes** |
| `nquark-signal-service` | 8003 | discovery / fetch / normalization (stateless) | no |
| `nquark-observation-service` | 8004 | append-only observation store (capture write target) | **yes** |
| `nquark-media-service` | 8002 | creative fetch + hash + graph link (byte storage OFF) | **yes** |
| `nquark-crawl-service` | 8001 | **always-on collector** (discover + capture) + enrichment + entity resolution + media hook | **yes** |
| Fly Managed Postgres | — | the one datastore (per-service Alembic version tables) | — |

All five are **private**: reachable only over the org's 6PN as `<app>.flycast`, **no public IP**, region
`sin` (Singapore, nearest to India). `auto_stop_machines = "off"`, `min_machines_running = 1`,
`force_https = false`. **DO NOT allocate a public IP.** After the first deploy, verify:
`fly ips list -a nquark-crawl-service` (there must be only a private Flycast address).

## Required app names (override via env)

`GRAPH_APP` `OBSERVATION_APP` `SIGNAL_APP` `MEDIA_APP` `CRAWL_APP` (defaults above). App names live only in
the `fly.toml` `app =` line and these env vars — never in application code (service discovery is env-driven).

## Required secrets (never in git)

Set per app with `fly secrets set` (read as plain env, not `NQUARK_`-prefixed):

| Secret | Apps | Purpose |
|---|---|---|
| `DATABASE_URL` | graph, observation, media, crawl | pooled application DB access (normalized to `postgresql+psycopg://`) |
| `MIGRATION_DATABASE_URL` | graph, observation, media, crawl | direct (unpooled) URL for Alembic/startup migrations; falls back to `DATABASE_URL` if unset |

`signal-service` needs no DB secret (stateless). `observation-service` attaches to the **same** shared
Managed Postgres cluster (`fly mpg attach <cluster-id> -a nquark-observation-service`, which sets the
pooled `DATABASE_URL`); its `MIGRATION_DATABASE_URL` is set to the cluster's `direct.<id>.flympg.net`
endpoint. Its Alembic version table is `alembic_version_observation`, so it coexists in the one database.

## Database attachment matrix (pooled vs direct)

Fly Managed Postgres exposes a **pooled** connection (PgBouncer) and a **direct** connection. Use:

- `DATABASE_URL` → the **pooled** endpoint for normal app traffic.
- `MIGRATION_DATABASE_URL` → the **direct** endpoint for migrations (DDL over a transaction pooler is
  unreliable). If you only set `DATABASE_URL`, migrations fall back to it.

```
fly secrets set -a nquark-crawl-service \
  DATABASE_URL='postgres://<user>:<pw>@<pooled-host>:5432/<db>' \
  MIGRATION_DATABASE_URL='postgres://<user>:<pw>@<direct-host>:5432/<db>'
# repeat for graph + media (same DB)
```

## Service URL matrix (Flycast, env-driven)

| Consumer | Variable | Value |
|---|---|---|
| crawl | `NQUARK_SIGNAL_SERVICE_URL` | `http://nquark-signal-service.flycast` |
| crawl | `NQUARK_GRAPH_SERVICE_URL` | `http://nquark-graph-service.flycast` |
| crawl | `NQUARK_MEDIA_SERVICE_URL` | `http://nquark-media-service.flycast` |
| media | `NQUARK_GRAPH_SERVICE_URL` | `http://nquark-graph-service.flycast` |

Local Docker/dev keep their `service:port` defaults — unchanged.

## Feature-flag matrix (cloud defaults, set in each `fly.toml [env]`)

| Flag | graph | signal | media | crawl |
|---|---|---|---|---|
| Shadow Ledger | `SHADOW_LEDGER_ENABLED=true` | `true` | — | (writes via graph) |
| entity resolution | — | — | — | `ENTITY_RESOLUTION_ENABLED=true` (boshow,district) |
| media observation | — | — | `MEDIA_OBSERVATION_ENABLED=true` | `MEDIA_OBSERVATION_ENABLED=true` (boshow,district) |
| media fetch | — | — | `MEDIA_FETCH_ENABLED=true` | — |
| media byte storage | — | — | `MEDIA_STORAGE_ENABLED=false` | — |
| collector | — | — | — | `COLLECTOR_ENABLED=true` (boshow,district) |
| **Skillbox** | — | `SKILLBOX_ENABLED=false` | excluded from `MEDIA_SOURCES` | excluded from every source-set |

Best-effort isolation is preserved: a media failure, an entity-resolution failure, or one source's failure
never fails a capture or another source.

## Deployment order & commands

```bash
# 0) operator (manual, cost-bearing): create the 4 apps + Fly Managed Postgres, attach + set secrets.
#    Do NOT allocate public IPs. Scripts never create paid resources.
scripts/fly-deploy.sh      # graph -> observation -> signal -> media -> crawl; health-gated; DRY_RUN=1 to preview
scripts/fly-bootstrap.sh   # one-time seed of an empty DB (Boshow+District; idempotent)
scripts/fly-smoke.sh       # private connectivity, health, migration heads, tracked events, Skillbox off
```

The crawl collector then runs continuously (discovery every `COLLECTOR_DISCOVERY_INTERVAL_SECONDS`,
capture every `COLLECTOR_CAPTURE_INTERVAL_SECONDS`). All state is in Postgres, so a restart resumes with
no lost tracked events, no duplicated work, and no reset Shadow Ledger.

## Rollback / pause procedure

- **Pause collection:** `fly secrets set -a nquark-crawl-service NQUARK_COLLECTOR_ENABLED=false` (redeploys;
  the API stays up, the loop stops) — or `fly scale count 0 -a nquark-crawl-service` to stop the machine.
- **Rollback a bad release:** `fly releases -a <app>` then `fly deploy --image <previous-image> -a <app>`
  (or `fly releases rollback`). Migrations are additive/reversible per service.
- **Resume:** re-enable the flag / `fly scale count 1`.

## Cost-bearing resources (operator-created only)

Running Machines (4 × shared-cpu-1x/512mb) and the Fly Managed Postgres cluster are the cost-bearing
resources. The scripts **never** create them — `fly apps create`, `fly pg create`, `fly volumes create`
and IP allocation are all explicit manual operator steps. No Fly Volume / Tigris / object storage is used
in this phase (media byte storage is disabled).
