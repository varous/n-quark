# Deployment notes — cloud services vs. the local-only inspection console

n-quark has two very different surfaces with two very different deployment stories:

1. **The pipeline + events feed** — deployed to the cloud (Fly.io). See
   [`deploy/fly/README.md`](../deploy/fly/README.md).
2. **The inspection console** (Admin Phases A–C) — a **local-only, unauthenticated** developer
   dashboard. It is **never** deployed to the cloud.

This document covers the boundary between them.

## The local-only boundary (enforced)

- The admin BFF (`/admin/v1`) is gated by `NQUARK_ADMIN_API_ENABLED` (default **false**). Every service
  `fly.toml` — including the public `nquark-api-gateway` — pins it **off**, plus
  `NQUARK_ADMIN_LOCAL_MODE = "false"`, so the admin surface returns `404` in the cloud.
- The Vite/React admin **frontend** has **no Fly app of its own** and is not built into any deployed
  image (the gateway `Dockerfile` builds only the Python service). It runs only from a developer machine.
- A test guards this: `tests/test_admin_phase_c.py::test_frontend_absent_from_cloud_deploy_manifests`
  and `::test_gateway_fly_manifest_pins_admin_off` fail if any manifest enables the admin API/local mode
  or references the frontend as a deployable path.

Cloud service deployment therefore continues exactly as before — **without** the dashboard.

## Running the local dashboard

```bash
# gateway in local inspection mode (repo root). Add the pipeline flags you want live data for.
ADMIN_API_ENABLED=true ADMIN_LOCAL_MODE=true \
ENTITY_RESOLUTION_ENABLED=true SCHEDULED_CAPTURE_ENABLED=true \
docker compose up -d --no-deps api-gateway

# frontend dev server
cd frontend && npm install && VITE_API_URL=http://localhost:8000 npm run dev
```

### Required local service dependencies

The BFF only reshapes live reads from the internal services, so bring up what you want to inspect:

| Service | Port | Needed for |
|---|---|---|
| `api-gateway` | 8000 | the BFF the browser talks to (the only surface the frontend calls) |
| `crawl-service` | 8001 | tracked events, capture jobs, enrichment, entity resolution, governance reads |
| `signal-service` | 8003 | source discovery/extraction (capture inputs) |
| `graph-service` | 8006 | canonical nodes/edges + Shadow Ledger timelines |
| `entity-service` | 8005 | (optional) health only |
| `postgres` / `redis` | 5432 / 6379 | datastore + scheduler locks |

The frontend dev server runs on **5173**.

### Environment flags

| Flag | Default | Effect |
|---|---|---|
| `NQUARK_ADMIN_API_ENABLED` | `false` | master gate for `/admin/v1`; `404` when off |
| `NQUARK_ADMIN_LOCAL_MODE` | `false` | **local-only**: no login/roles, single `INTERNAL_USER` context |
| `NQUARK_ADMIN_OPERATIONAL_ACTIONS_ENABLED` | `false` | governed/operational **mutation** endpoints; `503` when off |
| `NQUARK_ADMIN_DEV_AUTH_ENABLED` | `false` | isolated dev login (unused by the console in local mode) |
| `NQUARK_ADMIN_GRAPH_MAX_NODES` / `_MAX_DEPTH` | `150` / `2` | hard caps on the bounded graph explorer |

### How to disable admin mutations

The inspection console **never renders mutation controls**. To additionally block the governed/operational
BFF endpoints entirely (so even a direct `curl` is refused), leave
`NQUARK_ADMIN_OPERATIONAL_ACTIONS_ENABLED` unset or `false` — those routes then return `503`. The header
shows a `read-only` badge in that state. This is the recommended local default.

## Reminder

> **This dashboard is local-only and unauthenticated. Do not expose it publicly.**
