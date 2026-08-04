# Admin Console (frontend)

Internal observability console for n-quark. Vite + React 19 + Tailwind, no extra runtime deps. The app
boots into this console (`src/main.tsx` → `AdminApp`); the earlier demo panels remain in `src/components`
for reference but are no longer the entry point.

## Structure

- `api.ts` — typed client for the gateway admin BFF (`/api` → gateway `/admin/v1`), Bearer token from
  `localStorage`, graceful 401 handling.
- `auth.tsx` — `AuthProvider` / `useAuth` (role gate) + dev `Login` screen.
- `ui.tsx` — hash router (`useHashRoute`), `useAsync` (loading/error/reload), epistemic `Badge`, `Card`,
  `Stat`, `Table`, `Pager`, loading/empty/error/unavailable states, and the reusable provenance `Drawer`.
- `screens.tsx` — Overview, Sources, Events (list), Resolution, Captures, Health.
- `detail.tsx` — Event detail (tabbed), Entities (list + detail), Graph explorer (bounded SVG).
- `AdminApp.tsx` — shell: persistent nav, global search, breadcrumbs, hash routing.

## Conventions

- All data goes through the BFF (`api.ts`) — the browser never calls internal services directly.
- Filters/pagination are reflected in the hash URL where practical.
- State is shown with explicit **text** epistemic labels (never colour alone).
- The provenance drawer never renders raw third-party HTML/payloads.

## Dev

```bash
npm install
VITE_API_URL=http://localhost:8000 npm run dev   # http://localhost:5173
npm run build                                     # tsc -b && vite build
```

Requires the gateway running with `ADMIN_API_ENABLED=true ADMIN_DEV_AUTH_ENABLED=true`.
