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

Market-observatory information architecture (Phase 5B.2.6): **Overview** · **Explore**
(Events · Artists · Venues · Organizers) · **Monitor** (Watchlist · Market Movement · Demand) ·
**Coverage** (Collection · Captures) · **Advanced** (Data Quality · Analysis · Resolution · Graph ·
Diagnostics · All entities · System). A normal operator thinks in product terms — Events / Artists /
Venues / Organizers / Demand / Movement / Coverage — while the ontology (canonical / candidate / node /
edge / resolution status) stays under Advanced and Evidence.

- **Overview** answers "what is n-quark seeing?" with **registry-backed** product totals (Artists / Venues /
  Organizers from the canonical registry, never raw graph-node counts — via `/catalog/counts`), a
  Needs-attention grid over real supported states (open identity review, capture failures, content-movement
  & demand coverage) with honest explained-zero copy, and distinct demand-coverage vs service-reachability.
- **Event detail** leads with product tabs (Overview · Sources · Changes · Demand); Evidence · Entities ·
  Relationships · Capture status are grouped under **Advanced**. The Overview tab reads Event → Who & where
  (the integrity projection — a quarantined/placeholder mention is never shown as a link) → Ticketing.
- **Venue detail** is first-class (Activity · Artists appearing · Organizers active here · Events · Sources)
  from a bounded `/catalog/venues/{id}` read model — canonical relationships only, no frontend N+1.
- **Global search** is product-first: grouped Artists / Venues / Organizers / Events with plain type labels;
  quarantined/invalid canonicals and source projections never surface; review-required → "Needs data-quality
  review".
- **Data Quality** distinguishes **open** from already-**repaired** (quarantined) issues, and finishes the
  review toolset: a **canonical match selector** (link a mention to an operator-selected existing canonical,
  validated server-side) and compound review (suggested parts + Confirm-split re-resolution).

Global search, breadcrumbs, URL-backed filters, a reusable provenance drawer, and explicit epistemic labels
(Observed / Derived / Resolved / Ambiguous / Conflicting / Stale / Failed / Unknown) — never colour alone.

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

## Governed commands (Admin Phase B)

The console is now a governed workbench (see [ADR-0012](adr/0012-governed-resolution-decisions.md)). Every
mutation is role-authorized server-side, validated, audited, recorded as an append-only decision
(`admin_resolution_decision`, gateway-owned, Alembic migration `001`), idempotent on an idempotency key,
and reversible. Original source evidence is never deleted.

- `POST /admin/v1/resolution-decisions/preview` — impact preview (no mutation).
- `.../accept` `.../reject` `.../create-entity` `.../link-handle` `.../mark-alias` `.../mark-unresolved`
  `.../correct-series` — ANALYST.
- `.../supersede-legacy` — ADMIN (non-destructive: `legacy -SUPERSEDED_BY-> canonical`, legacy node + edges
  preserved, canonical counts dedupe superseded ids).
- `.../{decision_id}/reverse` — ADMIN (blocks with `REVERSAL_REQUIRES_MANUAL_DEPENDENCY_RESOLUTION` if a
  non-reversed decision depends on it).
- `GET .../resolution-decisions[/{id}]` — VIEWER.
- `POST /admin/v1/operations/capture-now` — OPERATOR/ADMIN; one targeted event through the normal
  scheduler → Shadow Ledger path, idempotent within a one-minute window; a failed request never becomes
  absence.

Reason is required for create / mark-alias / supersede / correct-series / reverse. Conflicts are explicit:
`STALE_PREVIEW`, `CANDIDATE_ALREADY_RESOLVED`, `HANDLE_ALREADY_LINKED`, `ENTITY_TYPE_MISMATCH`,
`LEGACY_ALREADY_SUPERSEDED`, `DECISION_ALREADY_APPLIED`. The **Resolution Workbench** (three panes:
source evidence · candidate entities · decision & impact) and the event **Capture ops** tab (capture-now)
drive these, role-gated in the UI *and* enforced server-side.

Extra flags: `ADMIN_OPERATIONAL_ACTIONS_ENABLED` also gates all governance commands (default off).

## Known limitations

- The audit table uses `create_all` (the gateway has no Alembic yet) with a SQLite fallback when the
  Postgres driver/DB is unavailable — a documented follow-up.
- "Capture one event now" is unimplemented (no safe targeted endpoint).
- The graph explorer is a compact SVG renderer (dependency-light); Cytoscape.js is a documented drop-in.
- `system-health` reports downstream `/health` payloads + data-quality aggregates; per-service migration
  versions are not yet surfaced.

## Phase 5A.2 — Demand Intelligence inspection (read-only)

A new **Demand Intelligence** domain in the same local console exposes the Phase 5A demand read models
(YouTube identity/observations, deterministic momentum, Google Trends, observed-supply context). It is
inspection-first, read-only, and local-only — see `docs/demand-intelligence.md` for the demand semantics.

BFF (`/admin/v1`, VIEWER, all degrade gracefully to `available:false` — never 500):
- `GET /demand/overview` — coverage + YouTube provider health (REAL/MOCK/UNKNOWN mode) + today's quota +
  read-only scheduler state + Google Trends OFFICIAL_API/IMPORT status.
- `GET /demand/summary` — compact dashboard headline.
- `GET /demand/artists/{artist_id}` — identities + YouTube + Trends + supply + momentum + geography bundle.
- `GET /demand/artists/{artist_id}/observations` — bounded, filterable observation history (limit ≤ 200).
- `GET /demand/events/{event_id}` — per-resolved-artist demand context + event-relative co-movement.

These proxy artist-intelligence-service (a new `artist_intelligence` downstream, port 8010); the browser
never calls it directly. There are **no demand mutation routes** — the whole surface is GET-only.

UI: a **Demand Intelligence** nav screen (coverage / provider / quota / scheduler / Trends), a full
**Artist Demand** view (also embedded as a section on the ARTIST entity page), an **Event → Demand
context** tab, and a dashboard summary card. Epistemic display rules are enforced (verification-explicit
identities, `confidence` ≠ popularity, subscriber rounding caveat, `INSUFFICIENT_HISTORY` /
`ACCESS_UNAVAILABLE` as legitimate states, Trends labelled relative-not-volume, "observed live supply").
A MOCK provider mode is rendered as an unmissable alert. No scheduler/resolve/refresh/import controls exist.

Boundary is unchanged: the frontend and admin BFF stay **local-only** (no Fly manifest references the
frontend; the admin flags stay off on cloud, enforced by test).

## Admin D — authenticated production console (`nquark-admin`)

The console is now also deployed as **one public HTTPS app** (`nquark-admin`, region `sin`) = the same
`api-gateway` codebase with the React SPA baked in, serving the SPA at `/` and the read-only `/admin/v1`
BFF same-origin, reaching the private services over Flycast. It is **operationally read-only** and gated
by **Google Workspace OIDC**. See `docs/deployment.md` for the deploy runbook.

**Google OAuth configuration**
- OAuth 2.0 **Web** client; consent screen scoped **Internal** (Workspace-only).
- Authorized redirect URI (exact): `https://nquark-admin.fly.dev/admin/v1/auth/callback`.
- Allowed sign-in domain: `NQUARK_OIDC_ALLOWED_DOMAIN=clockwork-av.com` (deny-by-default; optional
  `NQUARK_OIDC_ALLOWED_EMAILS` for named externals).
- Secrets (fly, never in git): `NQUARK_OIDC_CLIENT_ID`, `NQUARK_OIDC_CLIENT_SECRET`,
  `NQUARK_ADMIN_SESSION_SECRET`. The **client id must correspond to an existing OAuth client** or Google
  returns `Error 401: invalid_client` ("OAuth client was not found") at the login redirect.

**Security posture (Admin D.1)**
- **ID tokens are cryptographically verified**: the RS256 signature is checked against Google's JWKS
  (`https://www.googleapis.com/oauth2/v3/certs`, cache-controlled, key-rotation refresh); PyJWT enforces
  `aud`=our client id, `exp`, required claims; `iss` checked against Google's issuers; `alg=none` and
  unknown key ids rejected. A `nonce` is bound into the signed login `state` and re-checked on the token.
  Then `email_verified` + the Workspace-domain allowlist (fail-closed; `hd` must corroborate the email
  domain). Covered by `tests/test_oidc_verification.py` (forged / unknown-key / unknown-kid / alg=none /
  wrong-iss / wrong-aud / expired / unverified-email / wrong-domain / hd-spoof / nonce-mismatch rejected;
  valid Workspace identity accepted).
- **Session**: an httpOnly, `Secure`, `SameSite=Lax` cookie carrying an HMAC-signed principal (8h);
  stateless, so it survives machine restarts and works across HA machines. Logout clears it. The browser
  stores **no** token; no client secret / Google token / signing secret / Flycast hostname appears in the
  bundle, HTML, or API responses.
- **Hard read-only**: read paths reject POST/PUT/PATCH/DELETE (405); `/operations/*` require OPERATOR
  (403 for the console's VIEWER) and are additionally flag-disabled (503); governed
  `/resolution-decisions/*` require ANALYST (403 for VIEWER). Auth endpoints are exempt. Covered by
  `tests/test_admin_readonly.py`.
- **Environment identity**: `/auth/{status,me}` report `environment` (production/local) + `region` +
  `read_only`; the SPA shows a prominent **PRODUCTION · READ ONLY · SIN** badge so it never looks like
  local dev.
- **Downstreams**: only the six live Flycast services (crawl/graph/observation/signal/media/
  artist-intelligence) are wired; no localhost fallback (base map is docker service names in-container,
  overridden to `.flycast`). Un-deployed services (entity/analytics) degrade to `available:false`.

**Operational closure (Admin D.2)**
- **`/v1/platform/status` is authenticated.** The aggregate status enumerates internal service names +
  health (production topology), so it now requires the same read-only **VIEWER** principal as the rest of
  the console (`require_viewer`). Unauthenticated → **401** (production/OIDC); **404** when the admin API
  is disabled entirely; local single-context mode short-circuits to the internal user. The public
  **`/health`** endpoint (used by the Fly load-balancer check) stays open and returns only this app's own
  liveness — no downstream topology. Covered by `tests/test_admin_oidc.py`
  (unauth 401 / viewer 200 / admin-disabled 404 / local-mode open).
- **Always-warm.** As the operational production observatory, `nquark-admin` runs one machine always on
  (`min_machines_running=1`, `auto_stop_machines=false` in `deploy/fly/admin-console.toml`) so the console
  answers with no cold start; extra machines still auto-start under load. No collection-service machine
  config is affected.

## Market Observatory — product-facing UX (Phase 5B.2 increment 2)

The console reads as a market-intelligence terminal, not an entity/graph debugger. The backend ontology is
unchanged; the frontend simply stops making the operator reason in it.

- **Product-facing entity rule.** In normal screens, **Artist / Venue / Event / Organizer** mean the
  authoritative **canonical registry** entity. Artists and Venues lists come only from the crawl
  entity-resolution enumeration — **never raw graph artist-type nodes**. In production the registry has
  **64 canonical artists** while the graph has **102 artist-type nodes**; the extra 38 are
  `boshow:artist:*` **source-handle projections** (evidence, not additional artists) and appear only under
  Evidence/Advanced. A product count never uses a raw graph node-type total.
- **Navigation.** Explore (Events · **Artists** · **Venues** · Organizers) · Monitor (Watchlist · Market
  Movement · Demand) · Coverage (Collection · Captures) · Advanced (Analysis · Resolution · Graph ·
  Diagnostics · All entities · System). Artists and Venues are dedicated first-class screens; Graph /
  Resolution / raw entities are clearly Advanced.
- **Artist detail → "What n-quark knows" (Data Coverage).** Identity / Live activity / YouTube / Demand /
  Evidence in plain language, distinguishing four missing-data meanings the operator must not confuse:
  **ZERO_OBSERVED** ("No events observed yet"), **NOT_COLLECTED** ("YouTube monitoring has not started"),
  **UNAVAILABLE** ("Google Trends data is currently unavailable"), **INSUFFICIENT_HISTORY** ("Not enough
  history yet") — never a bare `0` / `N/A` / `—`.
- **Market Movement.** Observed abnormal YouTube movement across monitored artists, each card carrying its
  evidence (what/which artist/owned-or-ecosystem/vs what baseline/how much history). No rank, no virality
  score. When production has no verified channels (currently true), it shows an honest empty state pointing
  to the Watchlist rather than looking like a failure.
- **Terminology.** "Source listings" (not REPRESENTED_BY), "Artists" (not FEATURES / canonical ARTIST),
  "Related events/venues" (not graph neighbours). Raw ontology + provider ids remain under Evidence/Advanced.
- **Degraded downstreams** never break a product page: if artist-intelligence is down, Artists/Artist
  detail still render identity + live activity, with monitoring marked temporarily unavailable.

## Research configuration — Artist watchlists (Phase 5B.1)

The console is operationally read-only with **one deliberate exception**: RESEARCH CONFIGURATION. An
operator can add artists to a research watchlist (`Artists · Watchlist`) so the system starts trying to
observe them — no canonical id, YouTube id, SQL, or curl.

- **A watch target is not a canonical artist.** Adding one records a durable research *instruction*;
  n-quark then links it to an existing canonical (deterministic name match) or promotes it through the
  existing evidence rules. An operator instruction is a single independent discovery source, so a lone
  name with no existing canonical stays *pending* — canonical identity is never fabricated. Resolved
  targets flow into the existing demand pipeline (identity discovery → verification → catalogue → recurring
  demand). Pausing a target suspends its recurring collection without deleting history.
- **YouTube URLs are hints, not proof.** A pasted channel / `@handle` / video URL reduces resolution
  ambiguity but is still confirmed by the authoritative channels.list check before an identity resolves.
  External YouTube HTTP stays in signal-service (the single ingestion path).
- **Controlled-write boundary.** The write surface is `POST/GET /admin/v1/research/watchlist…`, kept
  structurally separate from the canonical/admin mutation routes. It is gated by
  `ADMIN_RESEARCH_CONFIG_ENABLED` (true on the console), requires the authenticated Workspace principal
  (VIEWER), records `created_by`, and is audited. It **cannot** mutate canonical entities, observations,
  graph nodes, provider observations, resolution outcomes, or event/historical state — those remain
  read-only. Unauthenticated → 401; disabled → 503.
- **Canonical-reference integrity (5B.1.1).** A target is shown as *Watching* only when its
  `canonical_artist_id` is acknowledged by the authoritative crawl entity-resolution registry (an uncached
  by-id check). A stale/promoted/operator-supplied id the registry does not own is never exposed as
  canonical — the target stays *Waiting for stronger evidence* with an auditable `canonical_unverified`
  note. `GET /admin/v1/research/watchlist/canonical-integrity` audits orphan canonical references across
  watch targets / candidates / identities / observations (never auto-rewritten).

## Lifecycle and source contribution (5B.3 Increment 2)

Event filters and read models consume backend temporal/provider lifecycle state; React performs no time
inference. Overview separates events observed, upcoming, happening now, and past. Artist and Venue detail
separate upcoming from past activity. The Sources coverage table now reports canonical events, cities,
temporal distribution, date/time and provider-lifecycle completeness, entity evidence including organizer
coverage, and existing freshness/health fields. Counts describe the supported canonical cohort only.
