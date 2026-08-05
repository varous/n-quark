# ADR 0014 — Content-addressed creative observation (Phase 4B)

- Status: Accepted
- Date: 2026-08-05
- Phase: 4B — Creative Asset Observation Service Scaffold
- Relates to: [docs/media-observation.md](../media-observation.md), media-service `README.md`,
  [ADR-0005](0005-shadow-ledger.md) (Shadow Ledger)

## Context

n-quark captures event data but never observed the **creatives** (posters/banners/listing images) events
present, or how they change over time. Phase 4B adds a deterministic scaffold for that — identity,
provenance, versions and transitions — **without** any visual intelligence (no OCR, recognition, colour,
embeddings, or scoring) and without media-service owning canonical event identity or event state.

## Decisions

1. **Content-addressed identity, source-independent.** Identity is the exact content **SHA-256**, then the
   normalized source URL when bytes aren't fetched. Identical bytes from different URLs are one asset;
   observations stay source-specific. Assets are never merged on filename. A perceptual-hash column exists
   for a future near-identical match but is left unset to stay dependency-free.

2. **Safe, bounded, SSRF-guarded fetching — opt-in.** The fetcher is HTTP(S)-only, blocks
   private/loopback/link-local/reserved targets (re-checked on every redirect hop), caps size and validates
   MIME by header **and** magic bytes, and limits redirects. It never bypasses access controls, CAPTCHAs,
   auth or anti-bot restrictions. Every outcome is classified. Fetching is gated by `MEDIA_FETCH_ENABLED`;
   with it off, observations are URL-only.

3. **A separate media transition history, not the Shadow Ledger.** Creative changes
   (`MEDIA_FIRST_SEEN` / `CONTENT_CHANGED` / `URL_CHANGED_SAME_CONTENT` / `ROLE_CHANGED` / `DISAPPEARED` /
   `REAPPEARED`) live in a dedicated `media_transition` table. The graph Shadow Ledger vocabulary is not
   modified. Rules are deterministic: a URL change with identical bytes is not a content change; a failed
   fetch is not a disappearance and preserves the last valid state; disappearance requires an authoritative
   successful capture with the reference absent; out-of-order observations never rewrite current state;
   re-observation is idempotent.

4. **Content-addressed local storage, bytes never in Postgres.** Bytes are keyed by hash on a local
   filesystem (`ab/cd/<sha>`), idempotent and de-duplicated; storage is disable-able (URL-only). No cloud
   bucket is required in this phase.

5. **Non-destructive graph link.** `event -USES_CREATIVE-> media_asset` is written best-effort via
   graph-service's node/edge upsert. media-service creates no canonical events and infers no
   artist/organizer/sponsor from the image. A graph-link failure never fails the observation.

6. **Best-effort capture integration; capture isolation is absolute.** After a successful capture,
   crawl-service notifies media-service (reading the event node's `image_url`). The hook is flag-gated
   (default off) and wrapped so **media processing can never fail the capture or Shadow Ledger update**.

7. **Everything flag-gated, additive, reversible.** All behaviour is off by default; existing capture
   behaviour is unchanged when disabled. The migration (`alembic_version_media`, `001`) is additive and
   reversible. No public creative-intelligence API; a stable internal `creative-summary` read contract is
   exposed for a later analytics phase, not wired into Phase 4A.

## Live evidence (2026-08-05, docker, real Boshow+District data)

Both adapters expose image references (Boshow `boshow.in/show_images/…`, District `media.insider.in`).
Observing a Boshow event resolved the reference from the graph node and fetched a real public asset
(`FETCHED`, `image/jpeg`, 1080×1448, 128 KB, content-addressed `storage_key`, SHA-256 identity); a District
event fetched a distinct asset. Re-observing the same content yielded no content-change (idempotent, one
asset). A bad image URL classified as `NOT_FOUND` and left the prior state untouched. The
`event -USES_CREATIVE-> media_asset` edge was written to the graph. Coverage reported observed references,
successful fetches and failures-by-class per source. A `capture-now` with media-service **up** produced a
capture trace `media: {MEDIA_OBSERVED, MEDIA_FIRST_SEEN, FETCHED}` and `SUCCEEDED`; with media-service
**down** the same capture still `SUCCEEDED` with `media: MEDIA_OBSERVATION_FAILED` — capture isolation
proven live.

## Consequences

- n-quark can now observe creative identity and change over time deterministically, with provenance and a
  full transition history, independent of any visual analysis.
- Perceptual/near-duplicate matching, role-change detection across roles, and analytics consumption of the
  creative-summary contract are natural follow-ups.
- Region/analytics phases can later fold creative-version counts in via the stable read contract.
