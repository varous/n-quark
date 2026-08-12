# Deployment notes — cloud services, the local console, and the authenticated production console

n-quark has three deployment stories:

1. **The pipeline + events feed** — the private collection spine on Fly.io. See
   [`deploy/fly/README.md`](../deploy/fly/README.md).
2. **The local inspection console** (Admin A–C) — an **unauthenticated developer** dashboard run from a
   machine in `ADMIN_LOCAL_MODE`. Never deployed.
3. **The authenticated production console** (Admin D, `nquark-admin`) — the **one public app**: the same
   React SPA + `/admin/v1` BFF, served from a single image, gated by **Google Workspace OIDC**, reaching
   the private Flycast services. **Operationally read-only.**

## The boundary invariants (enforced)

- **`ADMIN_LOCAL_MODE` is never `true` on any cloud manifest.** Local mode is the unauthenticated
  single-context bypass; on a public app it would expose the console with no login. Enforced by
  `tests/test_admin_phase_c.py::test_local_mode_never_enabled_on_any_cloud_manifest`.
- **The private service manifests + the crawl-space `nquark-api-gateway` never serve the console**
  (`ADMIN_API_ENABLED` stays off; no frontend). Enforced by
  `::test_private_service_manifests_do_not_enable_admin` and `::test_gateway_fly_manifest_pins_admin_off`.
- **The one public console (`deploy/fly/admin-console.toml`) is authenticated + read-only**: OIDC on,
  local mode off, operational actions off — enforced by
  `::test_admin_console_manifest_is_authenticated_and_read_only`. Secrets are never inlined in the manifest.

## Admin D — deploying the authenticated production console (`nquark-admin`)

The console is the same `api-gateway` codebase with the SPA baked in (`deploy/fly/admin-console.Dockerfile`,
repo-root context). Access requires a Google Workspace sign-in whose verified email is in the allowed
domain (`NQUARK_OIDC_ALLOWED_DOMAIN`, default `clockwork-av.com`; deny-by-default). All authenticated users
get a single read-only role; the session is an httpOnly signed cookie.

**Operator steps (cost-bearing / secret-bearing — done by a human):**

1. **Create a Google OAuth 2.0 Web client** (Google Cloud console → APIs & Services → Credentials).
   Authorized redirect URI: `https://nquark-admin.fly.dev/admin/v1/auth/callback` (match `PUBLIC_BASE_URL`;
   add your custom domain's callback too if you use one).
2. **Create the app + set the secrets** (never in git):
   ```bash
   fly apps create nquark-admin --org nquark
   fly secrets set -a nquark-admin \
     NQUARK_OIDC_CLIENT_ID=<google-client-id> \
     NQUARK_OIDC_CLIENT_SECRET=<google-client-secret> \
     NQUARK_ADMIN_SESSION_SECRET=<random 32+ bytes>
   ```
3. **(Optional, recommended) attach the shared Postgres** for the access/audit log (no new cluster; the
   console boots read-only without it — migrations are best-effort):
   ```bash
   fly mpg attach <cluster-id> -a nquark-admin          # sets DATABASE_URL (pooled)
   fly secrets set -a nquark-admin MIGRATION_DATABASE_URL='postgresql://…@direct.<id>.flympg.net/…'
   ```
4. **Deploy, then allocate the public IP** (the only app with one):
   ```bash
   fly deploy --config deploy/fly/admin-console.toml --dockerfile deploy/fly/admin-console.Dockerfile .
   fly ips allocate-v4 --shared -a nquark-admin && fly ips allocate-v6 -a nquark-admin
   ```

To restrict/extend who may sign in: set `NQUARK_OIDC_ALLOWED_DOMAIN` (the Workspace domain) and/or a
comma-separated `NQUARK_OIDC_ALLOWED_EMAILS` for named external accounts. Rotate
`NQUARK_ADMIN_SESSION_SECRET` to invalidate all existing sessions.

**Always-warm (Admin D.2).** `admin-console.toml` runs the console with one machine always on
(`min_machines_running=1`, `auto_stop_machines=false`) so it answers with no cold start — it is the
operational production observatory. Extra machines still auto-start under load. This is the console app
only; **do not change collection-service machine configs** for this.

**Restart runbook.** To restart only the console (never the collection fleet):
```bash
fly apps restart nquark-admin
```
Then re-verify over HTTPS — the gate and auth survive restart (the session cookie is stateless):
```bash
curl -s -o /dev/null -w '%{http_code}\n' https://nquark-admin.fly.dev/                     # 200 (SPA)
curl -s -o /dev/null -w '%{http_code}\n' https://nquark-admin.fly.dev/health               # 200 (public probe)
curl -s -o /dev/null -w '%{http_code}\n' https://nquark-admin.fly.dev/v1/platform/status   # 401 (authenticated)
```

**`/v1/platform/status` is authenticated (Admin D.2).** It lists internal service names + health, so it
requires the console's read-only VIEWER principal — unauthenticated returns **401**, not a public topology
dump. Only the plain `/health` liveness probe stays public (that is what the Fly health check hits).

**Research configuration (Phase 5B.1).** `NQUARK_ADMIN_RESEARCH_CONFIG_ENABLED=true` on `nquark-admin`
enables the one narrow authenticated write — artist watch targets (`/admin/v1/research/watchlist`). It is
independent of `NQUARK_ADMIN_OPERATIONAL_ACTIONS_ENABLED` (which stays false): research configuration
never touches canonical/observation/graph state. Deploying 5B.1 touches **signal-service** (adds
acquisition-only handle/video → channel resolution), **artist-intelligence-service** (adds the watch-target
table via migration 003, applied on boot), and **nquark-admin** (BFF + frontend). It does not touch the
crawl→graph→observation collection spine.

Cloud pipeline deployment is unchanged — the console is an additive, separate app.

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

> **`ADMIN_LOCAL_MODE` is the unauthenticated single-context bypass — it is for a developer machine only
> and must never be set on a cloud manifest.** The public production console (`nquark-admin`) is exposed
> only behind Google Workspace OIDC and stays operationally read-only.
