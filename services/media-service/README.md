# media-service

Observes public event **creatives** (posters, banners, listing images) over time — deterministically,
with content-addressed identity and provenance. It records asset identity, versions and changes; it does
**not** do visual intelligence (no OCR, face/logo recognition, colour analysis, embeddings, or scoring),
and it does **not** own canonical event identity or general event state.

All Phase 4B behaviour is **off by default** (feature-flagged). When disabled the service is health-only
and existing capture behaviour is unchanged.

## What it does

```
asset reference (from a captured event's image_url)
  → normalize URL
  → safe bounded fetch (SSRF-guarded, size/MIME-capped)   [optional; else URL-only]
  → content identity (SHA-256) + header metadata (w/h/mime/format)
  → content-addressed store (dedup by hash)               [optional]
  → record observation (idempotent) + deterministic transition vs current per (event, source, role)
  → append media transition history
  → best-effort graph link: event -USES_CREATIVE-> media_asset
```

Identical bytes from different URLs are **one asset** (source-independent). A URL change with identical
bytes is `MEDIA_URL_CHANGED_SAME_CONTENT`, not a creative change. A failed fetch never erases the last
valid state and is never a disappearance. Disappearance requires an authoritative successful capture
whose asset reference is absent. See [docs/media-observation.md](../../docs/media-observation.md) and
[ADR-0014](../../docs/adr/0014-content-addressed-creative-observation.md).

## Modules

- `identity.py` — SHA-256 content id + URL normalization (pure).
- `metadata.py` — dependency-free header parsing (PNG/JPEG/GIF/WEBP → mime/width/height/format).
- `transitions.py` — deterministic media-transition detector (pure).
- `fetcher.py` — safe bounded fetcher: HTTP(S) only, SSRF block (private/loopback/link-local/reserved,
  re-checked on redirects), size cap, MIME validation, redirect limit, classified results.
- `storage.py` — content-addressed local filesystem store (idempotent, disable-able; bytes never in PG).
- `service.py` — orchestration; `reads.py` — read models + coverage; `routes/media.py` — internal API.

## Internal API (`/v1/internal/media`, gated by `MEDIA_OBSERVATION_ENABLED`)

```
POST /observe                      (asset_url or resolve_from_graph; authoritative for disappearance)
GET  /assets            GET /assets/{id}
GET  /events/{id}       GET /events/{id}/timeline        GET /events/{id}/creative-summary
GET  /coverage          GET /failures
```
Filters: `source`, `asset_role`, `fetch_status`, `changed_only`, `limit`, `offset`. No public API.

## Feature flags (default off)

`MEDIA_OBSERVATION_ENABLED`, `MEDIA_FETCH_ENABLED`, `MEDIA_STORAGE_ENABLED`, `MEDIA_GRAPH_LINK_ENABLED`,
`MEDIA_MAX_BYTES` (5 MB), `MEDIA_FETCH_TIMEOUT_SECONDS` (10), `MEDIA_ALLOWED_MIME_TYPES`
(`image/jpeg,png,webp,gif`), `MEDIA_SOURCES` (`boshow,district`), `MEDIA_STORAGE_DIR` (`/data/media`),
`MEDIA_ALLOW_PRIVATE_NETWORKS` (false; tests only). Migrations run at boot when observation is enabled
(`alembic_version_media`, migration `001`, additive + reversible).

## Run (dev)

```bash
MEDIA_OBSERVATION_ENABLED=true MEDIA_FETCH_ENABLED=true MEDIA_STORAGE_ENABLED=true \
MEDIA_GRAPH_LINK_ENABLED=true docker compose up -d --no-deps media-service     # :8002
# capture-pipeline hook (crawl side, best-effort, never fails capture):
CRAWL_MEDIA_OBSERVATION_ENABLED=true docker compose up -d --no-deps crawl-service
```

## Invariants

- Content identity from bytes, not URL/filename; observations stay source-specific.
- Fetch failures don't create false disappearances or erase state; media never fails event capture.
- No OCR / recognition / embeddings / scoring. Read-only wrt canonical events; no new events created,
  no artist/organizer/sponsor inferred from the image. Flags default off; migrations additive/reversible.
