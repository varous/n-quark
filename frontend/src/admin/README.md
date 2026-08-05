# Inspection Console (frontend)

Internal, **local-only, unauthenticated** inspection console for n-quark. Vite + React 19 + Tailwind, no
extra runtime deps. The app boots into this console (`src/main.tsx` → `AdminApp`).

> **This dashboard is local-only and unauthenticated. Do not expose it publicly.**
> It is excluded from all cloud (Fly) deploy manifests and the admin BFF stays disabled in the public
> app. See [`docs/deployment.md`](../../../docs/deployment.md).

## Access model (Phase C)

There is **no login screen, no role selector, and no token**. When the gateway runs in local mode
(`NQUARK_ADMIN_LOCAL_MODE=true`) every request resolves to a single fixed `INTERNAL_USER` context that
satisfies all server-side checks. If the gateway is *not* in local mode the console shows a plain notice
(not a login form) — it never ships production auth. Google OAuth is deferred until/if the dashboard is
ever deployed.

## Structure

- `api.ts` — typed client for the gateway admin BFF (`/api` → gateway `/admin/v1`). No token is sent in
  local mode; a dev-auth Bearer token is attached only if one happens to be present.
- `auth.tsx` — `MeProvider` / `useMe` (reads `/auth/me` once for the header + local-mode check) and the
  `NotLocal` notice. No login, no roles.
- `ui.tsx` — hash router, `useAsync`, `useHashQuery` (URL-persisted filters), epistemic `Badge`, `Card`,
  `Stat`, `Table`, `Pager`, `ExportButtons`, loading/empty/error/unavailable states, provenance `Drawer`.
- `screens.tsx` — Overview, Sources, Events (search + filters + export), Captures, **Diagnostics**
  (per-source crawler health), Health.
- `detail.tsx` — Event detail (tabbed, incl. read-only Capture status), Entities (identity states,
  aliases, superseded, history, graph neighbourhood), Graph explorer (bounded, filtered).
- `workbench.tsx` — **inspection-first** Resolution diagnostics (the 5 uncertainty queues + evidence, no
  mutation controls).

## Conventions

- All data goes through the BFF (`api.ts`) — the browser never calls internal services directly.
- Event/graph/queue filters are reflected in the hash URL (`useHashQuery`) so views are shareable.
- State is shown with explicit **text** epistemic labels (never colour alone).
- The provenance drawer never renders raw third-party HTML/payloads.
- Exports (CSV/JSON) go through the BFF and respect the active filters (bounded).

## Run (local dashboard)

```bash
# 1) gateway in local inspection mode (from repo root; add the pipeline flags you want live data for)
ADMIN_API_ENABLED=true ADMIN_LOCAL_MODE=true \
ENTITY_RESOLUTION_ENABLED=true SCHEDULED_CAPTURE_ENABLED=true \
docker compose up -d --no-deps api-gateway

# 2) frontend dev server
cd frontend && npm install
VITE_API_URL=http://localhost:8000 npm run dev   # http://localhost:5173
npm run build                                     # tsc -b && vite build
```

- **Ports:** frontend `5173`, gateway `8000`, downstream services `8001`/`8003`/`8005`/`8006`.
- **Required flags:** `NQUARK_ADMIN_API_ENABLED=true` + `NQUARK_ADMIN_LOCAL_MODE=true` (both needed —
  local mode without the API flag still 404s).
- **Disable mutations:** leave `NQUARK_ADMIN_OPERATIONAL_ACTIONS_ENABLED` unset/`false`. The console
  never exposes mutation controls regardless; this flag additionally makes the governed/operational BFF
  endpoints return `503`, so even direct calls are blocked. The header shows a `read-only` badge.
