# ADR 0011 — Admin Console: BFF architecture & read models (Admin Phase A)

- Status: Accepted
- Date: 2026-08-04
- Phase: Admin Phase A — Canonical Data Inspection Console
- Relates to: [docs/admin-console.md](../admin-console.md), [ADR-0009](0009-second-source-and-reconciliation.md), [ADR-0010](0010-cross-inventory-entity-resolution.md)

## Context

n-quark now captures events, Shadow Ledger histories, enrichment evidence, canonical entities, source
handles and graph relationships across two sources — but there is no way to *inspect* what it observed,
what it believes, why, and where it is ambiguous/stale/failing. Admin Phase A builds the first internal
observability & data-governance console. It is an instrument panel, **not** an organizer dashboard,
commercial analytics product, or graph editor.

## Decisions

1. **BFF on the existing gateway, not a second gateway.** The admin surface is `/admin/v1/...` added to
   api-gateway (which already proxies internal services). The browser talks **only** to this BFF; it
   aggregates the existing internal APIs (crawl/graph/…) into frontend read models. Service ownership is
   unchanged — crawl owns capture/enrichment/entity-resolution operational data, graph owns nodes/edges,
   the Shadow Ledger stays the temporal authority. No canonical data is copied into a frontend store.

2. **Server-side auth + RBAC on every route; provider-neutral.** `authenticate(token) -> Principal`
   is the contract; the only implementation is an **isolated dev session** (HMAC-signed token from a
   flag-gated dev login) — never a production IdP, no hard-coded unrestricted admin. Roles VIEWER <
   ANALYST < OPERATOR < ADMIN are enforced by a `require_role` dependency (401 unauth, 403 under-privileged,
   404 when `ADMIN_API_ENABLED` is off). Frontend route-hiding is never relied on for authorization.

3. **Read-only, with two narrow audited OPERATOR actions.** Re-run enrichment / re-run entity resolution
   for **one** event — both target-bounded, OPERATOR-only, flag-gated (`ADMIN_OPERATIONAL_ACTIONS_ENABLED`,
   default off), assigned a request id, and written to an audit log. "Capture one event now" has no safe
   targeted internal endpoint yet and is left unimplemented (reported as a gap, not faked). No bulk
   operations, lock release, or feature-flag editing.

4. **Graceful degradation.** Every read model tolerates a disabled/unavailable downstream (returns partial
   data + an `available:false` marker), never a 500. The graph explorer is **hard-bounded** server-side
   (`ADMIN_GRAPH_MAX_NODES`, `ADMIN_GRAPH_MAX_DEPTH`) — it never loads the whole graph.

5. **Legacy vs canonical identity is observed, not migrated.** Phase 3.1 left the ingest-time naive
   entity projection coexisting with the evidence-based canonical layer. The admin API surfaces an
   explicit `identity_state` (`CANONICAL` / `ALIAS_LINKED` / `POSSIBLE_DUPLICATE` / `UNRESOLVED`) and
   shows both the canonical and the source-projected/legacy node on relationships — so the duplication is
   never silently hidden ahead of a later unification migration.

6. **Additive, default-off, reversible.** New crawl read endpoints (capture-job list/detail, canonical
   entity list/detail, single-event resolve) are additive; all admin flags default off; the audit table
   is created idempotently (the gateway has no Alembic yet, so `create_all` + a SQLite fallback when the
   Postgres driver/DB is unavailable — dropping the table is the reverse). Existing service APIs and the
   MCP are untouched.

## Frontend

Repo convention is Vite + React 19 + Tailwind (not Next.js), so the console is built there: a hash-router
SPA (persistent nav, global search, breadcrumbs, URL-backed filters, tables, a reusable provenance drawer,
explicit epistemic labels — never colour alone). The graph explorer is a compact SVG renderer of the
BFF's bounded subgraph rather than a Cytoscape install, to keep the build dependency-light; Cytoscape
remains a documented drop-in alternative.

## Consequences

- One authenticated instrument panel over all of n-quark's evidence and beliefs, with server-enforced
  RBAC and a clean seam for Google Workspace OIDC later.
- The dev audit table uses `create_all` (no gateway Alembic yet) — a documented follow-up.
- "Capture one event now" is a known gap pending a safe targeted internal endpoint.
