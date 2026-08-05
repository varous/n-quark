# Creative-asset observation & transitions (Phase 4B)

media-service observes public event creatives over time. Deterministic, content-addressed, and
non-destructive. **No visual intelligence** (no OCR, recognition, colour, embeddings, or scoring).

## Asset identity (deterministic order)

1. **Exact content SHA-256** — the primary, source-independent identity. Identical bytes from different
   URLs resolve to one `media_asset`.
2. **Normalized source URL** — the fallback identity when bytes aren't fetched (URL-only mode).
3. *Perceptual hash* — a column exists for a future near-identical match; unset in this phase (kept
   dependency-free).

Assets are never merged on filename alone. Observations remain source-specific even when they point at
the same content asset.

## Asset roles

`POSTER`, `THUMBNAIL`, `HERO_IMAGE`, `LISTING_IMAGE`, `UNKNOWN`. A missing reference creates no asset.

## Models

- `media_asset` — one row per content SHA-256 (mime, size, width/height/format, storage_key, fetch_status,
  first/last seen).
- `media_observation` — one sighting: (event, source, role, url, observed_at), asset id (nullable),
  fetch_status/http_status/content_type/error_class, trace id. Idempotent on the window.
- `event_media_state` — the current creative per (canonical_event_id, source, asset_role): current
  asset/url, `present`, first_seen, last_changed, `version`.
- `media_transition` — append-only, source-specific creative-change history (kept **separate** from the
  graph Shadow Ledger vocabulary).

## Fetch classification

`FETCHED`, `NOT_FOUND`, `SOURCE_UNAVAILABLE`, `BLOCKED`, `INVALID_CONTENT`, `TOO_LARGE`, `TIMEOUT`,
`UNSUPPORTED_TYPE`. The fetcher is HTTP(S)-only, SSRF-guarded (private/loopback/link-local/reserved
addresses blocked, re-checked on every redirect hop), size-capped and MIME-validated, with a redirect
limit. It does not bypass access controls, CAPTCHAs, authentication or anti-bot restrictions.

## Transitions

`MEDIA_FIRST_SEEN`, `MEDIA_CONTENT_CHANGED`, `MEDIA_URL_CHANGED_SAME_CONTENT`, `MEDIA_ROLE_CHANGED`,
`MEDIA_DISAPPEARED`, `MEDIA_REAPPEARED`.

Rules (deterministic):
- identical content at a new URL → `MEDIA_URL_CHANGED_SAME_CONTENT` (not a content change; version stays);
- different bytes → `MEDIA_CONTENT_CHANGED` (version bumps);
- a **failed fetch** is not a disappearance — the last valid state is preserved, no transition;
- **disappearance** requires an authoritative successful source capture with the asset reference absent;
- **out-of-order** observations are retained but never rewrite current state;
- re-observing the same identity is **idempotent** (no new transition).

## Capture-pipeline integration

After a successful capture (`SUCCESS_RECORD_PRESENT`), crawl-service best-effort notifies media-service
(`resolve_from_graph` reads the event node's `image_url`; `authoritative` marks a successful capture so an
absent reference is a real disappearance). **Media processing never fails the capture or Shadow Ledger
update** — the hook is wrapped in try/except and gated by `MEDIA_OBSERVATION_ENABLED` (default off).

Both current adapters expose usable references: Boshow (`show_image_link` → `boshow.in/show_images/…`)
and District (`absoluteBannerImageUrl` / JSON-LD `image`, on `media.insider.in`). Coverage is reported
per source and labelled **observed** creative coverage — never total creative-market coverage.

## Graph link

`event -USES_CREATIVE-> media:{asset_id}` (a `media_asset` node), with `{source, asset_role,
last_observed, present, version}`. Best-effort; a graph-link failure never fails the observation. No
canonical event is created, and no artist/organizer/sponsor is inferred from the image.

## Analytics contract (not wired into Phase 4A yet)

`GET /v1/internal/media/events/{id}/creative-summary` → creative-version count, unique creative count,
first observed, last changed, content changes, sources — a stable read contract for a later analytics phase.
