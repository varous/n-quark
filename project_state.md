# n-quark — Project State

_Last updated: 2026-08-07 (Phase 4D). Branch `main`. Repo: github.com/varous/n-quark._

n-quark is an India-first "Intelligence OS for live entertainment": 10 FastAPI microservices +
React frontend on Docker Compose (postgres/pgvector, neo4j-optional, redis, qdrant, minio).
It feeds **crawl-space** (a separate discovery/ticketing product) via the events feed. The strategic
moat is an **independent, cross-platform temporal observation layer** (see `docs/product-spec.md`, the
append-only MCP — never overwrite it).

## Delivered phases (all committed + pushed to `main`)

| Phase | What | Key artifacts |
|---|---|---|
| MCP augmentation | Added "Independent Market Observation and Temporal Data Moat" additively to the MCP | `docs/product-spec.md` |
| 1 — Shadow Ledger | Immutable `event × observed state × timestamp × evidence`; deterministic detector (no LLM); `?trace=true` chain | graph-service `shadow*`, ADR-0005 |
| 1.1 — Capture integrity | snapshot_completeness, field_status, effective-state carry-forward, out-of-order + conservative disappearance | `shadow-detector-2` |
| 2 — Scheduled capture | `tracked_event` + `scheduled_capture_job`; cadence bands, lease-lock + idempotency, retry/backoff; failed request never becomes absence | crawl-service scheduler, ADR-0006 |
| 2.1 — Enrichment | candidate→resolution; field registry; deterministic resolver; on-sale intervals (never fabricate exact times) | crawl-service `enrichment/`, ADR-0007 |
| 2.2 — Source-family / pilot | surface/source_family/independence_group; proved Boshow public page adds no new fields (OG-only, same family) → not promoted | ADR-0008 |
| 3 — Second source + reconciliation | Independent source **District** (schema.org JSON-LD); per-origin independence; bounded blocking + deterministic matcher; linkage without truth collapse; field reconciliation across independence groups | crawl-service `reconciliation/`, ADR-0009 |
| 3.1 — Cross-inventory entity resolution | Resolve exclusive Boshow/District events onto **shared canonical artists/venues/organizers/series**; per-type deterministic resolvers + ambiguity policy; source-handle registry + history; graph IDENTIFIES/ORGANIZED_BY/PART_OF_SERIES; shared entities never imply duplicate events | crawl-service `entity_resolution/`, migration 005, ADR-0010, `docs/entity-resolution.md` |
| Admin A — inspection console | First internal observability console: gateway **BFF** (`/admin/v1`) with server-side auth + RBAC (VIEWER/ANALYST/OPERATOR/ADMIN), read models over crawl/graph/Shadow-Ledger, bounded graph explorer, `identity_state` (legacy-vs-canonical visible), audited OPERATOR actions; Vite/React frontend (8 screens, provenance drawer, epistemic labels) | api-gateway `admin/`, frontend `src/admin/`, ADR-0011, `docs/admin-console.md` |
| Admin B — governed workbench | **Governed** entity resolution: gateway Alembic (migration 001; audit + append-only `admin_resolution_decision`), accept/reject/create/link/mark-unresolved/**correct-series**/**supersede-legacy**/**reverse** commands with RBAC + impact preview + idempotency + conflicts; non-destructive legacy supersession + dedup counting; year-only series safeguard; safe targeted **capture-now** via the normal scheduler path; three-pane Resolution Workbench + capture-now UI | api-gateway `db/` + `routes/admin_commands.py`, crawl `governance.py` + migration 006, frontend `workbench.tsx`, ADR-0012 |
| Admin C — local inspection hardening | Collapsed RBAC to a single **local-only, unauthenticated `INTERNAL_USER`** context (`ADMIN_LOCAL_MODE`; no login/roles); event search + rich filters (q/city/date/capture-state/resolution-status) URL-persisted; per-source **crawler diagnostics** (success rate, failure classes, parser failures, field present/valid/placeholder/missing); richer **system-health** (per-service version/flags/last-check, feature flags, data-quality set); bounded filtered **CSV/JSON export**; inspection-first Resolution (5 uncertainty queues, mutation controls removed); graph entity-type/source filters + cap warning; **local-only deploy boundary** (fly.toml pins admin off; frontend excluded, guarded by test) | api-gateway `admin/service.py` + `routes/admin.py` (export/diagnostics), frontend `screens.tsx`/`workbench.tsx`/`detail.tsx`/`auth.tsx`, `docs/deployment.md` |
| 4A — canonical market read models | Deepened analytics-service with a **non-destructive canonical query projection** (fold `SUPERSEDED_BY`/alias, cycle/invalid-chain protection) + deterministic read models: **regional observed-supply**, **artist/venue/organizer/series activity**, **observation-quality**, **commercial-state** (Shadow Ledger facts only, per-source prices separate). Counts by canonical id (legacy/superseded folded, never double-counted); bounded/paginated/stable-sort; `trace=true` explains inclusion/exclusion + folds + metric defs. New `/v1/analytics/market/...` surface; legacy scoring endpoints untouched. No prediction/scores/total-market claim; query-time (no new tables) | analytics-service `projection.py`, `readmodels.py`, `datasource.py`, `crawl_client.py`, `routes/market.py`, ADR-0013, `docs/analytics.md` |
| 4B — creative asset observation | Built **media-service** (Phase 4B scaffold): observe public event creatives over time — **content-addressed identity** (SHA-256 → normalized URL → optional phash), **safe SSRF-guarded bounded fetcher** (http(s)-only, private-net/redirect/size/MIME guards, 8 classified outcomes), **content-addressed local storage** (dedup, disable-able, bytes never in PG), dependency-free header **metadata**, deterministic **media transitions** (FIRST_SEEN/CONTENT_CHANGED/URL_CHANGED_SAME_CONTENT/ROLE_CHANGED/DISAPPEARED/REAPPEARED) in a dedicated history, `event -USES_CREATIVE-> media_asset` graph link, bounded internal APIs + coverage/failures + stable creative-summary contract. Best-effort crawl capture hook (never fails capture). No OCR/recognition/embeddings/scoring; flags default off; migration `001` additive/reversible | media-service `identity/metadata/transitions/fetcher/storage/service/reads/routes`, migration 001, crawl `media_notifier.py` + hook, ADR-0014, `docs/media-observation.md` |
| 4C — shared ticketing adapter + Skillbox | One typed **TicketingAdapter contract** (discover/fetch_event/normalize_event/classify_failure/extract_source_handles/extract_asset_references) wrapping the existing providers — Boshow/District/Skillbox conform, no regression. Deterministic **quality validation** before enrollment (12 rejection reasons; verified-city geography, tz-aware date normalization, present/valid/specific field status); **validated discovery** partitions accepted/rejected/out-of-scope; per-source **quality/coverage diagnostics** (`/v1/internal/sources/...`, observed-supply only). Skillbox third-source pilot flag-gated; pipeline parity config-driven (add `skillbox` to crawl source-sets). Bounded **Soundcharts feasibility** (no impl, no fabricated endpoints) + `ArtistIntelligenceProvider` proposal separating ticketing supply from licensed artist intelligence | signal `adapters/contract.py`+`quality.py`+`sources.py`+`routes/sources.py`, ADR-0015, `docs/ticketing-adapters.md`/`skillbox-probe.md`/`soundcharts-feasibility.md` |

## Phase 3 — LIVE VALIDATED (2026-08-04, full docker stack)

Docker build recovered (earlier BuildKit network deadline cleared). Brought up postgres/redis/
graph-service/signal-service/crawl-service with Phase 3 flags on. Confirmed live:

1. **District discovery + extraction** through signal-service `?source=district` — real schema.org
   fields (name, venue, city, region, startDate+tz, price, image) in ~0.4 s.
2. **Scheduled-capture parity** — enrolled 8 District events via scheduler `/sync?source=district`,
   captured into the Shadow Ledger with canonical ids (district 8/8, boshow 11/11). This surfaced and
   **fixed a real bug**: `/sync` never forwarded `source` to the discover call, so a second source
   could not be enrolled through the live scheduler path (commit `34d8e04`, + regression test).
3. **Source-selection probe** live (District vs Skillbox). Honest finding: the presence-only
   `structured_metadata_score` ranks Skillbox marginally higher (0.833 vs 0.817) because it counts
   *placeholders* as present — live Skillbox previews return `venue="Mutiple Cities, India"`,
   region duplicating city, and a placeholder date (`2029-07-28`, tz-naive). The **qualitative**
   inspection (real venues/cities/dates) is what correctly selects District; the automated score alone
   is insufficient. District remains the chosen source (ADR-0009).
4. **Reconciliation run** live over both sources (11 boshow × 8 district): blocking correctly rejects
   every pair → **0 matches, 0 false matches**. This is the truthful expected outcome — the cohorts are
   genuinely disjoint (Boshow = Kolkata grassroots: mindful walks, folk nights, indie art; District =
   nationwide mainstream: Ramoji, F1 sims, Imagicaa, Mystery Rooms). No live overlap exists yet, so
   **no live match is fabricated**. Match/scoring/linkage/field-reconciliation mechanics are proven by
   the 122 fixture-backed crawl tests.

## Phase 3.1 — LIVE VALIDATED (2026-08-04, full docker stack)

Entity resolution enabled over the real Boshow+District cohorts (migration 005 applied at boot):
- 19 events resolved (17 SUCCEEDED, 2 PARTIAL). Coverage: ARTIST 24/26 (2 ambiguous: `Pilu`, `BWS`
  correctly queued), VENUE 19/19, ORGANIZER 8/8, SERIES 2/2. Graph +48 `IDENTIFIES` / +8 `ORGANIZED_BY`
  / +2 `PART_OF_SERIES`.
- `Skinny Mos` (a Kolkata venue that also appears as a performer) → `venue:skinny-mos--kolkata` +
  `artist:skinny-mos`; repeat events converged via `SOURCE_HANDLE_MATCH` (handle registry works).
  `THE ABOMINATION XII` → `series:the-abomination` (edition 12 preserved as a distinct event).
- **Live cross-source entity overlap = 0** (Boshow-Kolkata-grassroots vs District-mainstream are
  disjoint) — reported honestly. Convergence mechanics proven with a **labeled fixture pair**
  (Kolkata/Boshow + Mumbai/District, both "Prateek Kuhad") → one `artist:prateek-kuhad`, two source
  handles, while reconciliation of the same two events yielded **0 matches** (shared entity ≠ duplicate
  event). Fixture rows removed after.

## Admin Phase A — LIVE VALIDATED (2026-08-04, full docker stack + browser)

Gateway BFF + Vite/React console, live over the real Boshow+District data:
- Auth: 401 unauthenticated; dev login issues HMAC session; role gate enforced server-side (VIEWER
  op → 403, VIEWER audit → 403, OPERATOR op → 200, ADMIN audit → 200).
- Dashboard: 19 tracked, 23 captures, 19 transitions, 0.92 artist rate, 2 ambiguous, boshow/district
  source cards, attention queues — rendered in the browser.
- Entities: identity_state badges; `Skinny Mos` as ARTIST **and** VENUE (4 events each, handle reuse).
- Legacy-vs-canonical visible: event relationships show both `venue:the-urban-theatre-project` (naive)
  and `venue:urban-theatre-project--kolkata` (Phase 3.1) → `POSSIBLE_DUPLICATE` surfaced, not hidden.
- Event detail tabs incl. Shadow Ledger timeline (District `EVENT_FIRST_SEEN`); bounded graph explorer
  (26 nodes/26 edges around THE ABOMINATION XII, PART_OF_SERIES + IDENTIFIES edges).
- OPERATOR `rerun-entity-resolution` on one event → ok, request id, **persisted to `admin_audit_log`**.
- Downstream-unavailable and graph node/depth caps enforced (tests + service).

## Admin Phase B — LIVE VALIDATED (2026-08-04, docker + browser)

Governed workbench live over the real Boshow+District data:
- Gateway migration `001` applied at boot (version surfaced in system-health); crawl migration `006`
  (`entity_supersession`) applied.
- ANALYST created `artist:pilu` from the ambiguous `Pilu` candidate; idempotent re-submit →
  `already_applied`; `expected_status` mismatch → 409 `STALE_PREVIEW`; VIEWER → 403.
- ADMIN superseded legacy `venue:the-urban-theatre-project` → `venue:urban-theatre-project--kolkata`
  (legacy node + edges preserved, `SUPERSEDED_BY` edge added; canonical count 47→46, superseded 1;
  ANALYST → 403).
- ANALYST `CORRECT_EVENT_SERIES` unlinked the weak `F1 2026` year-series; the year-only series
  safeguard prevents such auto-creation going forward.
- ADMIN reversed the `Pilu` create → candidate back to `AMBIGUOUS`; the Resolution Workbench shows the
  full history `— → AMBIGUOUS → RESOLVED (MANUAL_CREATE) → AMBIGUOUS (REVERSED)`.
- OPERATOR `capture-now` on a real Boshow event ran the normal job path (authoritative absence),
  idempotent on repeat. Every command audited + recorded as an append-only decision.

## Admin Phase C — LIVE VALIDATED (2026-08-05, docker + browser)

Gateway rebuilt + recreated with `ADMIN_API_ENABLED=true ADMIN_LOCAL_MODE=true` (`--no-deps`; crawl
keeps its Phase 3.1/B flags). Live over the real Boshow+District data:
- `GET /admin/v1/auth/me` with **no token** → `{sub: internal-user, role: INTERNAL_USER, auth_mode: local,
  local_mode: true, mutations_enabled: false}`; the console opens straight to the dashboard (no login,
  no role selector), header shows `internal-user · local · read-only`.
- Event search `?q=skinny` → 4 boshow events where *Skinny Mos* is an artist/venue (search hits resolved
  entity names, not just ids); filters (source/city/capture-state/resolution/date/transitions/stale) are
  URL-persisted (`#/events?q=…&source=…`). Timelines: boshow `free-folk-nite` (tickets 10→30, fill
  0.2→0.57, date change — all `Observed`, per-transition evidence) and district `imagicaa-theme-park`
  (`EVENT_FIRST_SEEN`, source=district).
- **Diagnostics**: boshow 11 tracked / 100% success / 0 parser failures / geography present 1·valid 1·
  placeholder 0·missing 10; district 8 tracked. **Health**: per-service reachability + flags
  (crawl `entity_resolution=true`, graph `shadow_ledger=true`) + last-check; gateway migration `001`
  present; feature flags incl. `admin_local_mode=true`; data-quality set (missing venue 19, missing
  geography 18, superseded-driven `legacy_canonical_duplicates` 0).
- **Graph**: bounded subgraph around `artist:skinny-mos` (14 nodes/22 edges) with relationship +
  node-type + source filters and cap warning; the legacy `venue:skinny-mos` and canonical
  `venue:skinny-mos--kolkata` both visible (duplication surfaced, not hidden).
- **Export**: `/admin/v1/export/events?format=csv&source=boshow` (header + rows honouring the filter) and
  `format=json`; `source-diagnostics` export (2 rows). Unknown table → 404, bad format → 422.
- **Resolution** is inspection-first (5 uncertainty queues + evidence, **no** mutation controls). The
  live queues are currently empty (Pilu/BWS were curated to RESOLVED in Phase B — ambiguous_mentions=0);
  reported honestly, no ambiguity fabricated. The queue/status mechanism is covered by tests.
- **Deploy boundary**: gateway `fly.toml` pins `NQUARK_ADMIN_API_ENABLED="false"` +
  `NQUARK_ADMIN_LOCAL_MODE="false"`; no service manifest references the frontend; a test enforces both.

## Phase 4A — LIVE VALIDATED (2026-08-05, docker, real Boshow+District data)

analytics-service rebuilt + started (`:8007`) over crawl (8001) + graph (8006). Live:
- **Canonical projection**: `canonicalize/venue:the-urban-theatre-project → venue:urban-theatre-project--kolkata`
  (path + identity_state CANONICAL, no warnings); querying the legacy venue id returns the canonical
  view (1 event) — folded, counted once, non-destructive.
- **Regional supply**: 7 region/city groups — `region:west-bengal` 11 boshow events / 23 canonical
  artists / 8 venues; `region:maharashtra` 3 district events / 3 organizers; source distribution per row.
- **Artist** *Skinny Mos*: 4 events (2 upcoming / 2 completed), Kolkata, 4 longitudinal, venue folded to
  `venue:skinny-mos--kolkata`. **Venue** *Skinny Mos*: 4 events, 4 with state transitions,
  `DIRECT_SOURCE_GEOGRAPHY_ONLY`, 6 artists. **Organizer** *KICKASS ADVENTURES*: 2 events, multi-venue +
  multi-city recurrence indicators. **Series**: 2 strong (`series:the-abomination`, F1 simulator).
- **Observation-quality**: 19 tracked, 17 with 1+ transitions, 3 with 2+ observations, avg gap 26.07h,
  0 stale (by source: boshow 11/11 transitions, district 6/8). **Commercial-state**: 19 price
  observations, per-source prices kept separate (boshow median ₹499, district ₹549.5), 1 availability
  change, 1 date/venue change, 3 disappeared/reappeared — Shadow Ledger facts only, nothing estimated.
- **Filters**: `source=district` → 6 groups; `source=district&date_from=2026-08-05` → 0 (reported
  honestly — no district event starts after that date in the cohort). **Trace**: shows 19 included / 0
  excluded, the single superseded fold, and scope limitations. Cohort is small; aggregates reported as-is,
  no fixtures injected.

## Phase 4B — LIVE VALIDATED (2026-08-05, docker, real Boshow+District data)

media-service built + started (`:8002`) with observation/fetch/storage/graph-link flags on (migration
`001` ran at boot); crawl-service rebuilt with the best-effort media hook. Live:
- Both adapters expose image references — Boshow (`boshow.in/show_images/…`), District
  (`media.insider.in`). Observed a Boshow event via `resolve_from_graph`: real public asset **FETCHED**
  (`image/jpeg`, **1080×1448**, 128 KB), SHA-256 identity, content-addressed `storage_key`,
  `MEDIA_FIRST_SEEN`. District event fetched a distinct asset.
- **Duplicate content**: re-observing the same bytes → no content change, one asset (content dedup).
  **Failed retrieval**: a bad image URL → `NOT_FOUND`, prior state untouched (no false disappearance).
- **Graph link**: `event:free-folk-nite -USES_CREATIVE-> media:…` written. **Timeline** + **coverage**
  (per-source: events inspected, references, successful/failed fetches by class, unique assets) +
  **failures** all live; coverage labelled *observed* creative coverage, not total.
- **Capture integration + isolation**: `capture-now` on a present District event with media-service **up**
  → capture `SUCCEEDED`, trace `media: {MEDIA_OBSERVED, MEDIA_FIRST_SEEN, FETCHED}`; with media-service
  **down** → capture still `SUCCEEDED`, trace `media: MEDIA_OBSERVATION_FAILED`. Capture isolation proven.

## Phase 4C — LIVE VALIDATED (2026-08-05, docker, real Skillbox/Boshow data)

signal-service rebuilt with the shared adapter contract + Skillbox quality gate (flag-gated). Live:
- `/v1/internal/sources` lists boshow/district/skillbox each advertising the six contract capabilities.
- **Skillbox bounded Kolkata pass** (25 records fetched+validated): **0** accepted for Kolkata, **1**
  rejected `PLACEHOLDER_DATE` (real pre-sale shell `taba-chake-india-tour-2026-…`), **24** out-of-scope
  (other cities — the sitemap head is not Kolkata-first). Field quality: title 1.0/1.0/1.0, city
  1.0/1.0/0.76, venue 1.0/0.8/0.8, date 1.0/0.96/**0.0** (all Skillbox dates tz-naive) — the gate works
  and Skillbox is confirmed low-quality.
- One accepted (city-agnostic) Skillbox event normalized cleanly (`DANCE WORKSHOP…`, Bengaluru, venue
  flagged non-specific). **Boshow** validated **5/5** accepted (city+venue specific 1.0) — unaffected.
- **No live cross-source convergence claimed**: no Kolkata Skillbox event met validation in the bounded
  sample (disjoint-cohort finding persists) — reported honestly, no fixtures injected to inflate. Pipeline
  parity (discovery→validation→capture→Shadow Ledger→enrichment→entity→media) is config-driven and
  identical to District's proven path; entity/media hooks stay best-effort.
- **Soundcharts**: feasibility report + `ArtistIntelligenceProvider` proposal delivered as docs — no
  implementation, no fabricated endpoint availability, ticketing-supply kept separate from artist intel.

## Phase 4D — continuous private collection on Fly.io (2026-08-07)

Prepared crawl/signal/graph/media for always-on **private** collection (config + scripts, validated
locally; no paid Fly resources created). See `deploy/fly/README.md`, ADR-0016.
- **Topology**: four private Flycast apps (region `sin`, no public IP, `force_https=false`,
  `auto_stop_machines=off`, `min_machines_running=1`, `/health` checks) — `deploy/fly/{graph,signal,media,
  crawl}-service.toml`. Admin frontend / api-gateway / analytics-service **not** deployed.
- **DB**: `DATABASE_URL` (pooled) for app + `MIGRATION_DATABASE_URL` (direct) for Alembic (falls back to
  `DATABASE_URL`), both normalized `postgres://`→`postgresql+psycopg://`; alembic env uses the migration
  URL. `NQUARK_POSTGRES_URL` still overrides; local unchanged.
- **Collector**: new bounded, restart-safe in-process loop in crawl-service (`collector.py`,
  `COLLECTOR_ENABLED`) reusing the existing scheduler (discover+enroll via `sync_from_refs`, capture via
  `run_once`) — no second scheduler. Full-pipeline worker + one-shot `python -m crawl_service.bootstrap`.
  Skillbox excluded from the collector source-set.
- **Cloud flags**: Boshow+District on, Skillbox off; Shadow Ledger + entity resolution + media
  observation + media fetch on; media byte storage off. Best-effort isolation preserved.
- **Scripts**: `scripts/fly-{deploy,bootstrap,smoke}.sh` (deploy graph→signal→media→crawl, health-gated;
  never create paid resources; DRY_RUN supported).
- **Local validation**: all 4 fly.toml parse; all 4 images build; bootstrap idempotent (boshow+district
  tracked count stable at 109 across two runs); crawl container restart preserves state (134 tracked /
  138 jobs identical) and capture continues (SUCCESS_RECORD_PRESENT + entity resolution); media failure
  stays isolated. No Fly credentials supplied → no cloud mutations performed.

## Phase 4C.1 — Skillbox targeted probe & decision (2026-08-07, live)

Bounded discovery probe + decision gate (see `docs/skillbox-probe.md`). Findings: **no public
city-filtered event API** (browse APIs in lazy SPA chunks); `sitemap-event.xml` is 24,401 URLs in one
all-cities file (not paginated/city-ordered); `/events-kolkata` is a non-SSR shell; **stable `city_id`**
in `event-details` → a verified city-id map built from source evidence (`adapters/skillbox_cities.py`;
Mumbai=5, Bengaluru=1106620, … — **Kolkata intentionally absent**, 0 observed). Stratified 30-event
sample: **0 Kolkata**, skew to Bengaluru/Goa/Mumbai. **Classification: OPPORTUNISTIC_SOURCE** (real
Mumbai/Bengaluru inventory + stable ids, but 0 Kolkata, no targeted discovery, tz-naive dates, no artist
evidence) — **left disabled by default; no further Kolkata-specific work** per the stop condition. Full
pipeline **proven** on 2 real Mumbai events (`domi-jd-beck…`, `ad-design-show…`): capture
`SUCCESS_RECORD_PRESENT` → entity resolution `SUCCEEDED` → new canonical venues (`venue:antisocial-lower
-parel--mumbai`, Jio World) with `skillbox:venue:` handles; media hook fired (best-effort). **Convergence
= 0 real overlap** (new Skillbox-only venues; no duplicate event fabricated). Boshow/District unaffected.

## Test status
crawl **200** (+8 Phase 4D) · gateway **58** · signal **95** · graph **60** ·
analytics **45** · media **43** · observation **11** · entity **12** — all pass.
Media Alembic upgrade+downgrade verified. Frontend `tsc -b` + `vite build` clean. Lint clean except
baseline-tolerated B008 (FastAPI `Depends`) and one pre-existing S110 in an alembic migration.

## Invariants / constraints (must hold)
- Deterministic + explainable only; **no LLM** in detection/matching/enrichment.
- Never report a failed request as record absence (absence is authoritative 404 only).
- Never fabricate values or a live match; fixtures for mechanics must be labelled.
- Additive/reversible migrations; feature flags default **off**; existing behavior preserved.
- `docs/product-spec.md` (MCP) is append-only — never overwrite.
- No PII/fingerprinting; don't persist full third-party HTML indefinitely; images hotlinked not re-hosted.
- BookMyShow stays partner_feed (never evasively scraped); no CAPTCHA/bot-evasion.
- API keys: user adds to `.env`; never handled/pasted by the agent.
- The admin **inspection console is local-only + unauthenticated** (`ADMIN_LOCAL_MODE`) — never enabled
  on a cloud deploy; the admin BFF stays disabled and the frontend is excluded from all Fly manifests
  (enforced by test). See `docs/deployment.md`. OIDC is deferred until/if the dashboard is deployed.

## Recommended next phase
**Operator executes the Fly deploy** (create 4 apps + Managed Postgres, set `DATABASE_URL`/
`MIGRATION_DATABASE_URL` secrets, `scripts/fly-deploy.sh` → `fly-bootstrap.sh` → `fly-smoke.sh`, verify
`fly ips list` is private-only) and lets the collector run; then observe accrued Shadow Ledger history.
Then resume product work: fold media's `creative-summary` into analytics, add perceptual creative
matching, and (deferred) **paged Kolkata-first Skillbox discovery** (the sitemap head isn't Kolkata-ordered, so a city-targeted
paged crawl is needed to surface Kolkata inventory and make cross-source convergence non-zero), then run
Skillbox through the full capture pipeline (config-driven) and measure real convergence. In parallel: fold
media-service's `creative-summary` contract into analytics, add perceptual/near-duplicate creative
matching, and — when artist intelligence becomes a priority — the ~90-call Soundcharts proof-of-value set
behind a future `ArtistIntelligenceProvider`. Still deferred: unify the ingest-time naive projection with the
evidence-based canonical layer (one graph id convention) so analytics can retire the read-time
canonicalizer; a materialization layer for analytics if the cohort grows large; and driving a
canonical-vs-legacy unification migration from accumulated `SUPERSEDED_BY` decisions.
(1) Build minimal internal cross-inventory analytics on top of the Phase 3.1 canonical entities
(artist/venue/organizer footprints across sources, cities, series) — read-only, deterministic, no
prediction. (2) Reconcile the ingest-time naive entity projection (signal-service name-slug) with the
Phase 3.1 evidence-based canonical layer so the graph has one id convention. (3) Only then add a third,
city-overlapping source so cross-source entity convergence becomes non-zero in practice. Deferred:
matcher/threshold calibration against a real labelled overlap cohort, and accepted-match consensus
write-back — both still relevant once an overlapping source exists.
