# Social Signal Intelligence — Phase 5C.1: Social Evidence Foundation

The governed spine for social signal intelligence: **canonical entity → social identity →
watchlist-driven acquisition → SocialMention evidence → coverage/diagnostics**. This increment builds
the foundation only — not the full 5C pipeline. A social post is *evidence*, never canonical truth, and
never auto-creates a canonical Event.

Built to [docs/data-doctrine.md](data-doctrine.md): source expressive content is ephemeral; only
factual/semantic claims + provenance are persisted; there is no raw-post warehouse and no post
embeddings.

## Architecture

Mirrors the ticketing spine — external HTTP stays in **signal-service**; canonical association, evidence
persistence, and the watchlist scheduler live in **crawl-service** (the canonical owner); reads surface
through the **gateway** BFF. No new social microservice.

```
canonical entity (registry-owned)
  → SocialIdentity association (crawl-service)               [governed, auditable]
  → watchlist scheduler run (crawl-service)                  [bounded, canonical-driven]
      → (HTTP) signal-service /social/collect                [official Meta Graph seam; no scraping]
          → claims + provenance (raw caption consumed, dropped)
  → SocialMention evidence (crawl-service)                   [idempotent on platform+post_id]
  → gateway /admin/v1/social/* reads → admin "Social" coverage view
```

## What is implemented

- **`social_identity`** (crawl migration `008`): associates a public account (`platform`,
  `handle`/`platform_account_id`, `account_url`) with an existing `canonical_entity_id` +
  `canonical_entity_type` (ARTIST/VENUE/ORGANIZER). Carries `evidence_role`, `verification_state`
  (ASSERTED → VERIFIED only on a real provider confirmation; no AI), `active`, and watchlist scheduling
  state (`collection_state`, `next_eligible_at`, `last_collected_at`, `last_access_state`,
  `consecutive_failures`). Multiple accounts per canonical and per platform are supported; associations
  are auditable (`provenance.history`). Canonical ownership is unchanged — this only associates.
- **`social_mention`** (migration `008`, versioned in `009`): durable, provenance-bearing evidence —
  `platform`, `source_account`, `platform_post_id`, `post_url`, `published_at`/`observed_at`, resolved
  `canonical_entity_id`, `linked_canonical_entity_ids`, `extracted_claims`, `evidence_role`,
  `confidence`, `parser_version`, `content_hash`, `provenance`. **Append-only and versioned** — see the
  immutability section below. `processing_status`/`claim_type` are the **5C.2 seam** (see below).
  **There is deliberately no raw caption/media column.**
- **`SocialAcquisitionService`** (crawl): `link_identity`/`set_active` (governed), `watchlist`
  (eligibility), `run_once` (bounded pass: eligible identities → signal `/social/collect` → append-only
  versioned mention ingest → identity scheduling update), plus `coverage`/`identities`/`mentions` reads
  and `mention_history` (the full immutable version lineage of one logical post). A run never touches
  `TrackedEvent` or canonical events.
- **signal-service `adapters/social.py` + `/v1/signals/social/*`**: governed source descriptors
  (instagram/facebook/reddit), honest per-platform `access_state`, and `collect()` returning claims +
  provenance (raw caption consumed transiently, never returned). Includes a bounded official
  `_MetaGraphClient` seam for the PRODUCTION path.
- **gateway** `/admin/v1/social/{overview,identities,mentions}` (read-only) + a compact admin **Social**
  coverage view. No raw-source bulk export endpoint.

## Immutable evidence versions — a changed post never erases the earlier state (5C.1.1)

A **logical social post** is `(platform, platform_post_id)`; each materially different *observed content
state* is a distinct, **immutable** version of `social_mention`. This mirrors the repo's existing
temporal pattern (`event_field_resolution`: `version` + `is_current`, prior rows preserved, never
overwritten) and the Shadow-Ledger principle that an observed source state is never overwritten by a
later one.

```
logical source post  ≠  one mutable evidence row

each materially changed observed source state
   → a new immutable evidence version
```

- **First observation** → version 1, `is_current = true`, `previous_mention_id = null`.
- **Recapture, same content hash** → idempotent, no new row (no mutation). When the provider omits a
  hash, idempotency falls back to a deterministic comparison of the extracted claims, so correctness
  does not assume a hash is present.
- **Recapture, changed content hash** → **INSERT** a new version (`version = prev + 1`,
  `previous_mention_id` links to the prior row); the prior row's evidence fields are **never rewritten**
  — only its `is_current` pointer flips to false and `superseded_at` is stamped.

So a *Venue A* observed on the 10th survives a *Venue B* edit on the 12th and remains independently
queryable as version 1. Schema (additive migration `009`): the old unique index on
`(platform, platform_post_id)` is replaced by a **partial** unique index on that tuple `WHERE is_current`
(exactly one current version per logical post — the integrity invariant and the idempotent upsert
target) plus a `(platform, platform_post_id, version)` lineage index.

### Evidence vs derived interpretation — the boundary

| | fields | mutability |
|---|---|---|
| **Immutable observed evidence** | platform, post id, source account, published/observed_at, canonical association *at observation time*, extracted claims, content hash, parser version, confidence, provenance, version | never rewritten |
| **Mutable workflow / pointer metadata** | `is_current`, `superseded_at` (version pointer); `processing_status`, `claim_type` (the **5C.2 derived-interpretation seam**, unset here) | may change without altering what was observed |

5C.2 classification is *derived interpretation of* evidence — it must never overwrite the observed
source state. `claim_type` on the mention is workflow metadata only; the preferred 5C.2 design carries
the classifier verdict in a separate, independently versionable interpretation layer keyed to the
specific evidence version it interpreted, so re-classification never mutates evidence.

## Acquisition behaviour by platform

| platform | role | access without creds | with authorized Meta token |
|---|---|---|---|
| Instagram | OFFICIAL_ACCOUNT_EVIDENCE | `CREDENTIAL_UNAVAILABLE` (or `DISABLED`) | `PRODUCTION` (official Graph) |
| Facebook | OFFICIAL_ACCOUNT_EVIDENCE | `CREDENTIAL_UNAVAILABLE` (or `DISABLED`) | `PRODUCTION` (official Graph) |
| Reddit | COMMUNITY_EVIDENCE | `ACCESS_PENDING` (no approved path) | — (still pending) |

A deterministic offline **mock** (`meta_mock_mode` or a fixture) yields clearly-marked (`mock: true`)
claims so the spine is demonstrable without credentials. **Absent authorized access is honest**: the
scheduler records an `ACCESS_PENDING`/`DEFERRED` state and backs off — it never fabricates a failure and
never substitutes a scrape. A transient collection error backs the identity off without corrupting it.

## Retention & governance

Persisted: extracted claims, a `content_hash` (for change detection), and provenance
(`acquisition_method`, account, fetch time, `raw_content_retention: EPHEMERAL`). **Not persisted**: raw
captions, media, or any expressive content — there is no column for it, and the ingest path reads only
the claims/hash/provenance the adapter returns. Acquisition posture (`access_state`) is kept separate
from transformation posture, per the two-axis model in
[docs/source-governance.md](source-governance.md). No commenters, followers, group members, or
individual-user PII are collected; no login-wall/CAPTCHA circumvention.

## The 5C.2 seam (not implemented here)

`SocialMention.processing_status` defaults to `UNPROCESSED` and `claim_type` is unset — both are mutable
**workflow** metadata, not observed evidence (see the boundary table above). 5C.2 will add the
deterministic classifier that reads unprocessed evidence versions and derives a claim type
(ANNOUNCEMENT / TICKETING / LINEUP_CHANGE / VENUE_CHANGE / RESCHEDULE / CANCELLATION / SELL_OUT /
ADDITIONAL_SHOW / PROMOTION), then promotes strong evidence to an Event candidate through the **existing**
reconciliation machinery — governed, never auto-creating canonical Events. Because a changed post now
produces a new immutable version, 5C.2 can classify per version and can *later distinguish* `new post` /
`same post unchanged` / `same post edited` / `post observed again` from the version lineage without any
of that inference happening here. The preferred 5C.2 design keeps the classifier verdict in a separate,
independently versionable interpretation record keyed to the evidence version it interpreted, rather
than treating `claim_type` as source truth. In 5C.1/5C.1.1 a SocialMention stays evidence.

## Validation

Fixture/mock-validated (no Meta credentials in this environment): signal adapter access-state + mock
extraction + ephemeral-retention tests; crawl identity association / multi-account / idempotent ingest /
provenance / no-expressive-content / no-Event-creation / watchlist scheduling / access-pending /
transient-failure tests; **immutable-versioning tests** (first version; same-hash idempotency;
changed-hash → second immutable version with the first unchanged; hash-absent claims fallback; lineage
recoverable; multiple successive edits preserve all versions with exactly one current; no raw content on
any version; history read); gateway read-model + history tests. Migrations `008` **and `009`**
upgrade/downgrade/upgrade verified, and the `009` partial-unique invariant functionally enforced (one
current version per post). **Meta/Facebook/Reddit acquisition is NOT operational** — it is the governed
seam awaiting authorized access.

## Configuration (all OFF by default)

signal-service: `NQUARK_SOCIAL_ENABLED`, `NQUARK_INSTAGRAM_ENABLED`, `NQUARK_FACEBOOK_ENABLED`,
`NQUARK_REDDIT_ENABLED`, `NQUARK_META_APP_ID/SECRET`, `NQUARK_META_ACCESS_TOKEN`, `NQUARK_META_MOCK_MODE`.
crawl-service: `NQUARK_SOCIAL_ENABLED`, `NQUARK_SOCIAL_PLATFORMS`,
`NQUARK_SOCIAL_COLLECTION_INTERVAL_SECONDS`, `NQUARK_SOCIAL_MAX_IDENTITIES_PER_RUN`,
`NQUARK_SOCIAL_BACKOFF_SECONDS`, `NQUARK_SOCIAL_ACCESS_PENDING_RETRY_SECONDS`.

## Remaining blockers

Authorized Meta Graph access (App review + a long-lived Page/IG token) is required before any live
Instagram/Facebook collection. Reddit needs an approved commercial-access path. Until then the spine runs
in mock/deferred states only.
