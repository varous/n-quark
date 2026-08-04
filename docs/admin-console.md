# Admin Console (Admin Phase A)

An internal, authenticated observability & data-governance console. It answers: *what did n-quark
observe? what does it currently believe? why? which source supplied each claim? which relationships were
resolved? where is it ambiguous / conflicting / stale / failing?* It is an instrument panel — not an
organizer/audience product, commercial analytics, or a graph editor. Read-only except two narrow,
audited operational actions. See [ADR-0011](adr/0011-admin-console-bff.md).

## Architecture

```
Admin frontend (Vite/React)  ->  gateway admin BFF (/admin/v1)  ->  crawl-service / graph-service / …
```

The browser talks **only** to the BFF. The BFF aggregates existing internal APIs into read models; it
never copies canonical data into a frontend store, and service ownership is unchanged (crawl = capture/
enrichment/entity-resolution ops, graph = nodes/edges, Shadow Ledger = temporal authority).

## Authentication & roles

- Provider-neutral `authenticate(token) -> Principal`. Only implementation: an **isolated dev session**
  (HMAC-signed token from `POST /admin/v1/auth/login`), gated by `ADMIN_DEV_AUTH_ENABLED`. Not a
  production IdP; no hard-coded admin. A Google Workspace OIDC verifier can implement the same contract.
- Roles VIEWER < ANALYST < OPERATOR < ADMIN, enforced **server-side** on every route via `require_role`
  (401 unauthenticated, 403 under-privileged, 404 when `ADMIN_API_ENABLED` is off). Hidden UI buttons are
  never the security boundary.
- Phase A: VIEWER/ANALYST = all read screens; OPERATOR = the two operational actions; ADMIN = audit log
  (+ reserved for future config/user management).

## BFF endpoints (`/admin/v1`)

`auth/login`, `auth/me` · `dashboard` · `sources`, `sources/{source}` · `events`,
`events/{id}`, `events/{id}/timeline`, `events/{id}/evidence`, `events/{id}/relationships` ·
`entities`, `entities/{type}/{id}` · `resolution-queue`, `resolution-queue/candidates/{id}` ·
`capture-jobs`, `capture-jobs/{id}` · `graph/subgraph` (bounded) · `system-health` · `search` ·
`audit` (ADMIN) · `operations/rerun-enrichment`, `operations/rerun-entity-resolution`
(OPERATOR, flag-gated, audited).

All collection endpoints are paginated with bounded page sizes; downstream failure degrades to partial
data (`available:false`), never a 500.

## Screens

Overview (KPI cards + source summary + attention queues) · Sources · Events (list + detail tabs: Current,
Source records, Timeline, Evidence, Entities, Relationships, Capture ops) · Entities (list + detail with
identity state, source handles, linked events, candidates) · Resolution queue · Graph explorer (bounded)
· Captures · Health (services + data quality). Global search, breadcrumbs, URL-backed filters, a reusable
provenance drawer, and explicit epistemic labels (Observed / Derived / Resolved / Ambiguous / Conflicting
/ Stale / Failed / Unknown) — never colour alone.

## Canonical identity state

Phase 3.1 left the ingest-time naive projection coexisting with the evidence-based canonical layer. The
console makes this visible (never hides it) via `identity_state`:

- `CANONICAL` — resolved canonical with a registered handle.
- `ALIAS_LINKED` — handles from more than one source resolve to it (cross-source).
- `POSSIBLE_DUPLICATE` — e.g. a city-scoped `venue:name--city` whose non-scoped legacy sibling
  `venue:name` also exists as a canonical.
- `UNRESOLVED`.

Relationships show both the canonical target and the legacy/source-projected target when they differ.
This is *observation ahead of* a later canonical-unification migration, not the migration itself.

## Graph explorer bounds

Server-side hard caps: `ADMIN_GRAPH_MAX_NODES` (default 150), `ADMIN_GRAPH_MAX_DEPTH` (default 2). The
BFS caps nodes, drops edges to unmaterialized nodes, and reports `capped`. It never loads the whole graph.
Node types: event, artist, venue, organizer, event_series, region, source_handle. Relationships:
FEATURES, OCCURS_AT, ORGANIZED_BY, PART_OF_SERIES, IN_REGION, IDENTIFIES, REPRESENTED_BY.

## Operational actions & audit

Two OPERATOR actions (re-run enrichment / entity resolution for one event) — target-bounded, flag-gated,
each assigned a request id and written to `admin_audit_log` (actor, role, action, object, request id,
reason, timestamp). "Capture one event now" is a **known gap** — no safe targeted internal endpoint yet.

## Feature flags (default off)

`ADMIN_API_ENABLED`, `ADMIN_DEV_AUTH_ENABLED`, `ADMIN_OPERATIONAL_ACTIONS_ENABLED`, `ADMIN_SESSION_SECRET`,
`ADMIN_SESSION_TTL_SECONDS`, `ADMIN_GRAPH_MAX_NODES`, `ADMIN_GRAPH_MAX_DEPTH`. No admin data is exposed
when disabled.

## Running the console (dev)

```bash
# gateway with admin enabled + the pipeline flags (from repo root)
ADMIN_API_ENABLED=true ADMIN_DEV_AUTH_ENABLED=true ADMIN_OPERATIONAL_ACTIONS_ENABLED=true \
ADMIN_SESSION_SECRET=dev-secret ENTITY_RESOLUTION_ENABLED=true ... docker compose up -d api-gateway
# frontend
cd frontend && VITE_API_URL=http://localhost:8000 npm run dev   # http://localhost:5173
```

## Known limitations

- The audit table uses `create_all` (the gateway has no Alembic yet) with a SQLite fallback when the
  Postgres driver/DB is unavailable — a documented follow-up.
- "Capture one event now" is unimplemented (no safe targeted endpoint).
- The graph explorer is a compact SVG renderer (dependency-light); Cytoscape.js is a documented drop-in.
- `system-health` reports downstream `/health` payloads + data-quality aggregates; per-service migration
  versions are not yet surfaced.
