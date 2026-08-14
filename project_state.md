# n-quark — Project State

_Last updated: 2026-08-14 (Phase 5B.2 increment 7 — YouTube collection recovery). Branch `main`. Repo: github.com/varous/n-quark._

## Phase 5B.2 increment 7 — YouTube collection recovery (2026-08-14, DEPLOYED)

Made the stalled YouTube demand pipeline actually acquire real data. Deployed artist-intelligence +
nquark-admin only. No migration.

**Diagnosis (live prod)**: 67 canonical artists; YouTube identity **0 RESOLVED / 37 AMBIGUOUS /
39 UNRESOLVED**; `GENERAL_READ = 0` every day (channels.list never invoked); videos = 0; scheduler
`queued_due 0 / next_refresh null`. Three root causes:
1. **Verification never ran for ambiguous** — `resolve_youtube` only called `channels.list` after a
   search-only RESOLVED decision; real artists rarely clear the 0.70/0.20 threshold on search snippets
   alone → AMBIGUOUS forever, GENERAL_READ 0.
2. **Obsolete quota model + double-count** — `search.list` costed at 100 units (should be 1, independent
   100/day quota); `total_used` summed the SEARCH bucket AND its `SEARCH:<purpose>` sub-buckets → 35
   calls showed `used_total 7000`.
3. **Scheduler terminal for non-RESOLVED** — only RESOLVED identities were re-queued; AMBIGUOUS/UNRESOLVED
   were never re-attempted.

**Fixes**:
- **Verification wiring (§7/§8)**: `resolve_youtube` authoritatively verifies the top-N PLAUSIBLE
  candidates via `channels.list` (not only pre-declared leaders), re-scores on the enriched metadata
  (a verified exact-title match earns a bounded bonus), then decides among the verified pool. The
  clear-leader margin still guards equally-named impostors — thresholds are NOT lowered.
- **Quota model (§5/§6)**: `search.list` = 1 unit in an INDEPENDENT 100-call/day Search-Queries quota;
  general reads draw the 10,000-unit pool; `general_used` excludes SEARCH + its sub-buckets (kills the
  2× double-count); the snapshot reports the two quotas separately with a `reconciles` invariant.
- **Scheduler (§9/§10)**: `enqueue_identity_reattempts` keeps AMBIGUOUS/UNRESOLVED schedulable on a
  status-based cadence; eligibility gated on the crawl product registry (invalid/orphan/compound
  canonicals excluded; fails closed if the registry is unavailable). Owned uploads persist
  `relationship_type=OWNED_CONTENT` explicitly (§14). Terminology (§18/§19): coverage + Demand UI
  distinguish **verified channels** from **identity candidates**; adds a `youtube_pipeline` funnel.
- Also fixed a latent bug: the registry read requested `limit=500` (crawl caps at 200 → 422 → silent
  fail-closed); now 200.

**Production proof (real, bounded — §24 ladder A→F reached)**:
- **A** quota corrected live: `reconciles: true`, `used_total 0` (was the 7000 double-count), SEARCH
  reported independent (`cost_per_call 1`).
- **B** `artist:arijit-singh` **UNRESOLVED → RESOLVED, verified=true** via `channels.list`
  (`UCtFOW7jJXChfFNoucRFqRmw`); `artist:sounak-chattopadhyay` correctly stayed **AMBIGUOUS
  (verified_no_clear_leader)** — verification ran, impostor protection held.
- **C/D** verified channel → **50 owned uploads** via the official uploads playlist (not search),
  50 registered as **OWNED_CONTENT / CHANNEL_UPLOADS**.
- **E** **152 real content observations** (views/likes/comments) collected via videos.batchGetStats
  (first observation; second follows on the next cadence — not fabricated).
- **F** scheduler now **queued_due 67 / identity_jobs_due 67 / next_scheduled_refresh set** (was null);
  **skipped_invalid=8** (the orphan/compound canonicals excluded). `GENERAL_READ 0→7`, verified_channels
  `0→1`, owned videos `0→50`.
- Honest caveat: today's SEARCH bucket carries stale old-model units (3500), so the AUTOMATIC scheduler
  search path DEFERS (never fails/invalidates) until the next Pacific quota reset, after which the 67
  re-attempt jobs flow. The manual bounded requeue proved the full causal chain now works.
- Tests: artist-intelligence **129** (+8 recovery; quota tests updated to the current model), signal
  **107**, gateway **137**. frontend tsc/build/lint clean.



## Phase 5B.2 increment 6 — market observatory UX closure (2026-08-13)

Final product-comprehension pass over the trusted interpretation layer, before returning to YouTube
collection. No entity-resolution architecture change (one governed-correction bug fixed). No migration.
Affected services: crawl-service (audit/correction) + nquark-admin (gateway BFF + frontend).

- **Information architecture** — the console reads as a market observatory: Overview / Explore
  (Events·Artists·Venues·Organizers) / Monitor (Watchlist·Market Movement·Demand) / Coverage
  (Collection·Captures) / Advanced (Data Quality·Analysis·Resolution·Graph·Diagnostics·All entities·
  System). Dead infra-first Overview removed from `screens.tsx`.
- **Overview redesign (§4–6)** — product-first: "what n-quark is seeing" from the **canonical registry**
  (Events observed, Artists/Venues/Organizers identified, Artists monitored, Sources active) via a new
  `/catalog/counts` BFF (registry totals, never raw graph-node counts); a **Needs-attention** grid backed
  by real states (open identity-review items, capture failures, content-movement / demand coverage) with
  honest explained-zero copy; demand-coverage + service-reachability cards kept distinct from evidence.
- **Venue detail (§10–11)** — first-class: Activity, **Artists appearing here**, **Organizers active
  here**, Events, sources — from a new bounded `/catalog/venues/{id}` read model (server-side graph
  fan-out capped at 60 events, **canonical nodes only** — source-handle projections excluded; no frontend
  N+1, no full-graph pull).
- **Event detail (§8)** — product-first tabs (Overview·Sources·Changes·Demand) with the technical tabs
  (Evidence·Entities·Relationships·Capture status) grouped under **Advanced**; the Overview tab leads with
  Event → Who&where (integrity projection) → Ticketing; raw resolved-view fields moved under Advanced.
- **Events list (§7)** — product columns (Event·Date·City·Source·Observed changes·Status); resolution/
  state/transition counts no longer lead the market list.
- **Global search (§18–19)** — product-first: grouped Artists/Venues/Organizers/Events with plain type
  labels + product routes; reads the suppressing `/entities` endpoint so quarantined/invalid canonicals
  and source projections never surface; review-required entities labelled "Needs data-quality review".
- **Data Quality repaired/open (§22)** — `quality_audit` now distinguishes **open** vs already
  **repaired** (governed-quarantined) issues: `open_issues`/`repaired_issues` + per-item `state`/`repaired`;
  repaired items stay in the manifest for provenance but are **excluded from open counts**. UI shows them
  struck-through with a "Repaired · quarantined" chip and no action.
- **Review toolset closure (§20–21)** — **canonical match selector**: a review mention can be linked to an
  operator-selected **existing** canonical via `CONFIRM_EXISTING_MATCH`, now **fixed** to actually set +
  **validate** the target (`_is_known_canonical` — must be a real canonical of the same type; arbitrary ids
  rejected end-to-end), audited with prev/new canonical. **Compound review**: ambiguous compounds show the
  suggested parts read-only with "Confirm split" (re-resolves each part independently — never a combined
  canonical) + Leave-unresolved. (Interactive part-editor that *creates* child mentions still deferred —
  needs a new governed SPLIT action.)
- Tests: crawl **233** (+3: audit repaired/open, CONFIRM_EXISTING_MATCH links selected canonical + rejects
  arbitrary id), gateway **137** (+5: catalog counts×2, venue aggregation×2, confirm-existing-match
  forwarding). frontend tsc/build/lint clean.
- **Remaining**: full Demand default-view redesign (§13–15, coverage-first sections + Advanced ops),
  interactive compound part-editor (governed SPLIT), and the authenticated live production walkthrough +
  Fly reconciliation (operator-side — agent cannot complete OAuth).



## Phase 5B.2 increment 5 — entity-integrity product closure (2026-08-12, DEPLOYED)

Closed the gap between the integrity backend and the product experience. No migration; crawl + nquark-admin only.

- **Event integrity projection**: `interpreted_relationships(event)` + `/events/{id}/interpreted-entities` —
  a quarantined/placeholder mention is NEVER presented as a linked entity. Venue reads **NOT_ANNOUNCED**
  (raw source preserved), review/role-conflict mentions read **NEEDS_REVIEW**, only RESOLVED canonicals are
  real links. Gateway `event_detail` includes `interpreted`; the Event page leads with a "Who & where"
  card in plain language ("Venue not announced", "N artists identified · M need review") — never inferring
  integrity from graph edges/strings.
- **Data Quality review workflow**: the screen gains a live **Needs review** queue with context-sensitive
  governed actions (Confirm dual-role on ROLE_CONFLICT, Re-run, Reject — candidate-scoped, authenticated,
  audited) + a **Suppress placeholder** action on the audit manifest + a **quality-metrics** strip.
- **Quality-flow metrics**: `quality_metrics` + `/quality-metrics` — clean_single / placeholder_suppressed /
  compound_split / role_conflict / review_required / resolved / operator_corrected, by type + source, open
  review items, oldest review age, recently corrected, `interpretation_method=deterministic` (AI disabled).
- **Production proof (real, read-only except the inc4 quarantine)**:
  - **§16 flagship**: the two real District events that referenced `venue:venue-to-be-announced--kolkata`
    (`ramayan-2026-buy-tickets`, `dirty-drivez-2025-buy-tickets`) now project **venue NOT_ANNOUNCED,
    canonical null, raw "Venue to be announced" preserved** — no link to the quarantined venue; still
    visible under Advanced (`include_flagged`); zero rows deleted.
  - **§13 metrics**: 823 mentions processed — 805 clean_single / 11 compound_split / 815 resolved / 5
    ambiguous / 1 unresolved / 2 quarantined / **0 open review** (the 16 audit cross-type cases are all
    established dual-role, correctly resolved by the refined gate, not queued).
  - **§19 regression**: re-resolving 20 District/Boshow events → **20/20 SUCCEEDED**; placeholder stays
    suppressed from product Venues.
- Tests: crawl **230** (+3 interpreted/metrics), gateway **132** (+2). frontend tsc/build/lint clean.
- **Remaining**: the full compound-review split editor + existing-canonical select-search UI (§7/§8 — APIs
  exist; UI wires the core actions), and the deferred Part B market-observatory UX (Overview/Events list/
  Venue detail/Demand). Live ROLE_CONFLICT review items appear only for NEW conflicts (current prod cross-
  type are all legitimate dual-role, surfaced via the audit rather than the live queue).


## Phase 5B.2 increment 4 — integrity enforcement + governed corrections (2026-08-12, DEPLOYED)

Closed the integrity loop: detection → **enforcement → review → governed correction → safe historical
repair**. No schema migration (new states live in the existing `resolution_status` column + `evidence` JSON).

- **Resolver gate**: `resolve_event` now consumes interpretation before any canonical create/match.
  Placeholder → suppressed (no candidate); compound → split children resolve independently, **never a
  combined canonical**; ambiguous compound → **REVIEW_REQUIRED**; a **new** cross-type identity →
  **ROLE_CONFLICT** (no wrong-type canonical). Interpretation is persisted on the candidate
  (`evidence.interpretation`). New states REVIEW_REQUIRED / ROLE_CONFLICT / PLACEHOLDER / QUARANTINED.
- **Dual-role protection**: the cross-type gate fires only for a **new** wrong-type creation — an
  established same-type canonical resolves normally even when the identity also exists as another type, so
  legitimate dual-role entities (a venue that also organizes) are never suppressed on re-resolution.
- **Product suppression**: `entities()` excludes canonicals whose every candidate is non-product
  (quarantined/review) unless `include_flagged`; the catalog **and** global search read that endpoint, so
  suppressed entities vanish from Artists/Venues/Search together.
- **Governed corrections** (crawl `service` + `/review-queue`, `/correct`): `review_queue` (live gated
  mentions), `quarantine_canonical` (suppress a bad canonical — status→QUARANTINED, **evidence + history
  preserved, zero rows deleted**), `apply_correction` (MARK_PLACEHOLDER / CONFIRM_MULTI_ROLE / REQUEUE /
  REJECT_MATCH / CONFIRM_EXISTING_MATCH) — actor + prev/new audited in `evidence.corrections` + history.
  Gateway `/admin/v1/data-quality/{review-queue,correct}`: authenticated (Workspace VIEWER), flag-gated
  (research-config), **operator forwarded as `actor` (never client-supplied)**, gateway-audited. Data
  Quality screen gains a governed "Suppress placeholder" action + human issue copy.
- **AI adjudicator** stays a runtime-DISABLED provider-neutral seam; the deterministic pipeline + review
  queue work without it.
- **Production proof (real, governed)**: fresh dry-run **520 audited / 503 clean / 1 placeholder + 16
  cross-type**. The flagship **`venue:venue-to-be-announced--kolkata`** ("Venue to be announced", district):
  **before** = in the product Venue list; governed **quarantine** (2 candidates, 0 deleted, evidence
  preserved); **after** = **gone from the product Venue list**, still under Advanced (`include_flagged`).
  **Regression cohort**: re-resolving **20 District/Boshow events → 20/20 SUCCEEDED** (no coverage loss),
  placeholder stays suppressed/not recreated, legitimate dual-role entities resolve normally (review queue 0).
- Tests: crawl **227** (+11 across interpretation/enforcement: placeholder-no-candidate, compound split,
  cross-type→review, dual-role protection, quarantine preserves evidence, multi-role audited), gateway
  **130** (+5 correction auth/flag/action/actor + review read). frontend tsc/build/lint clean.
- **Remaining**: Event "Venue not announced" copy (§14 — the event read model still references the venue via
  graph; catalogue/search already suppress it), the full per-mention review-card action set in the UI
  (§17 — API supports all actions; UI wires MARK_PLACEHOLDER), quality-flow metrics dashboard (§19), and
  the Part B market-observatory UX (Overview/Events/Venue/Demand).


## Phase 5B.2 increment 3 — entity-integrity core + data-quality audit (2026-08-12, DEPLOYED)

Entity integrity was the explicit priority. Delivered the deterministic, evidence-bounded interpretation
core + a read-only production audit; the operator-correction workflow, historical-repair execution, and
the Part B UX closure (Overview/Events/Venue/Demand) remain follow-ups.

- **Deterministic interpretation library** (crawl `entity_resolution/`): `placeholders.py` (conservative
  absence classifier — "Venue to be announced"/TBA/TBD/…, never fires on legitimate names with common
  words), `compound.py` (staged parser — protects known band names with &/+/comma, splits confidently-known
  or comma-lineup lists, else **AMBIGUOUS_COMPOUND** = no blind split), `interpretation.py` (role-aware
  orchestrator: placeholder → compound → cross-type/role-conflict → OK; source role kept as evidence;
  never creates canonicals), `adjudicator.py` (**provider-neutral `EntityAdjudicator` seam +
  `DisabledAdjudicator`** — AI is runtime-DISABLED, no credential configured; ambiguous → REVIEW_REQUIRED).
- **Pipeline gating** (`evidence.extract_event_entities`): placeholder venues/organizers/artists are
  **suppressed** (raw kept for audit), confident **compound artist strings split** into separate mentions,
  singles unchanged, band names protected. New events now resolve correctly; existing canonicals untouched.
- **Read-only audit + dry-run manifest** (`service.quality_audit` + `/quality-audit`): classifies every
  canonical (PLACEHOLDER / COMPOUND / CROSS_TYPE) with proposed action + **auto_safe vs review**. Gateway
  `/admin/v1/data-quality` + a **Data Quality** screen under Advanced.
- **Production audit (real, read-only)**: **460 canonical entities, 447 clean (97%)**; flagged **1
  placeholder** — the exact `"Venue to be announced"` the brief named, AUTO-SAFE — and **12
  cross-type conflicts** (mostly legitimate dual-role entities: venues that also organize — DORANGOS,
  Tripbae, The Social House; a comedian who performs + organizes — Abijit Ganguly), correctly **review-gated,
  not auto-repaired**. No mutation performed.
- Tests: crawl **221** (+19: placeholder suppression, compound split, band protection, cross-type conflict,
  adjudicator-disabled, pipeline integration). gateway **125**, frontend tsc/build/lint clean.
- **Remaining (next increment)**: operator correction workflow (§15), historical-repair execution (§16/17;
  the placeholder is flagged auto-safe but not yet suppressed via a governed path), live cross-type gating
  in the resolver + REVIEW_REQUIRED state persistence, and Part B UX (Overview/Events/Venue detail/Demand,
  §19/23/24/26) + search integrity (§27).



> **Phase 5B.2 is in progress (multi-increment).** Increment 1 delivered the deterministic content-movement
> backend + Data Coverage/Market Movement read models + the canonical preflight. **Increment 2 (this update)**
> delivered the **Market Observatory frontend** — first-class Artists/Venues from the canonical registry,
> prominent Artist Data Coverage, honest Market Movement, a product-facing nav, organizer de-clutter — plus
> the bounded catalog BFF. Both deployed. **Still not done**: ecosystem content discovery + relevance
> (§11–14) and movement-aware scheduler cadence (§6). See the 5B.2 section below.


n-quark is an India-first "Intelligence OS for live entertainment": 11 FastAPI microservices +
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
| 5A — public demand intelligence | New **artist-intelligence-service** (port 8010): demand-side layer meeting supply only through `canonical_artist_id`. Own **demand ledger** (`artist_external_identity`, `artist_demand_observation`, `provider_quota_day`, `demand_refresh_job`; separate from the event Shadow Ledger; append-only, idempotent on `observation_key`). **Reuses signal-service for YouTube acquisition** (extended with acquisition-only `search`+`videos`) — no parallel ingestion path, API key stays in signal-service. Provider-neutral contract (capability flags); deterministic YouTube identity resolution (RESOLVED/AMBIGUOUS/UNRESOLVED, name-equality never resolves alone, never creates a canonical artist); rounded-subscriber honesty (`PROVIDER_REPORTED`); per-provider/day **quota accounting** (search 100u vs read 1u, budget-enforced); restart-safe **refresh scheduler** (lease/retry/idempotent, known-id reads only); Google Trends **OFFICIAL_API (gated → ACCESS_UNAVAILABLE) + IMPORT** (labeled CSV, no scraping); first-class **geography** (ISO IN-XX); deterministic **momentum/geography/supply-demand/event-response** read models (no score, no causal claim, `INSUFFICIENT_HISTORY` honesty). Separate service so demand failure never disrupts the crawl→signal spine | artist-intelligence-service `providers/`+`service.py`+`scheduler.py`+`intelligence.py`+`supply.py`+`signal_client.py`, migration 001, signal `adapters/youtube.py`+`routes/youtube.py`, ADR-0017, `docs/demand-intelligence.md`+`docs/providers/{youtube,google-trends}.md` |
| 5B.1.1 — watchlist canonical-integrity closure (deployed) | **Closed the "orphan canonical" vector; explained the 5B.1 Arijit report.** Diagnosis: the report's "Arijit not in crawl registry" was a **stale/timing artifact** — the MULTI_SOURCE `create_artist` call registers via `_register_external_artist`, so `artist:arijit-singh` is registry-backed. Prod audit: **0 orphans** across watch targets / candidates / identities (62) / observations; registry (64), graph (102), target, candidate, demand **all agree**. Hardening (invariant: WATCHING may only reference a canonical the crawl registry acknowledges): new `crawl_client.canonical_artist_registered(cid)` = uncached authoritative **by-id** check (`/entities/ARTIST/{id}`; graph-only node → 404 → False); `promotion.promote` re-reads/confirms the resolved id before link/onboard (a stale `ALREADY_LINKED` candidate id is no longer trusted → `CANONICAL_UNVERIFIED`) — closes it for every caller; `watchlist.resolve_target` verifies operator-selected + promoted canonicals before exposing them (unbacked → RESOLUTION_PENDING + `detail.canonical_unverified`, never WATCHING; crawl outage fails closed); new `canonical_integrity` audit + `/research/watchlist/canonical-integrity` (+ gateway BFF). Canonical/provider identity stay distinct (no parallel namespace; provider-only never fabricates a canonical). **Prod-proved**: by-id True/False (fail-closed), integrity 0 orphans, and the **URL-hint path is verification-gated** — real forHandle + channels.list calls (REAL mode) returned NOT_FOUND for `@arijitsingh` / a stale id and **refused to resolve/fabricate** any identity | artist-intel `crawl_client.py`+`promotion.py`+`watchlist.py`+`routes/watchlist.py`, api-gateway `admin/watchlist.py`+`routes/admin.py`, tests `test_watchlist.py`+`test_promotion.py`+`test_admin_watchlist.py` |
| 5B.1 — artist intake & research watchlists (deployed) | **Operators can now say "start watching this artist" — no ids/SQL/curl/provider knowledge.** New `artist_watch_target` (migration 003, artist-intel) = a durable operator research instruction, NOT a canonical artist. `watchlist.py` reuses the EXISTING candidate/promotion/identity/onboard machinery: idempotent intake (dedup canonical›channel›name), resolve = link to an existing canonical or promote through the existing evidence rules — **operator intake is one independent discovery source, so a lone name stays RESOLUTION_PENDING and never fabricates a canonical**; a pasted YouTube URL (`yturl.py` parses channel id/@handle/video) is a hint that is **provider-verified via channels.list** before any identity resolves (`service.resolve_youtube_from_hint`); resolved targets onboard into the existing demand pipeline; pause suspends recurring scheduling (via `enqueue_due`, never deletes history); bulk preview/add (bounded, idempotent); diagnostics. signal-service added acquisition-only `resolve/handle` (channels.list forHandle) + `resolve/video` (videos.list channelId) — reuse the one ingestion path. Gateway: `WatchlistAdminService` + `/admin/v1/research/watchlist` = the **one narrow controlled WRITE (RESEARCH CONFIGURATION)**, structurally separate from canonical/admin mutations, flag-gated (`ADMIN_RESEARCH_CONFIG_ENABLED`), authenticated (VIEWER), records `created_by`, audited; canonical/observation/graph stay read-only from this surface. Frontend: **Artists · Watchlist** screen (add one / paste URL / bulk, human-readable states, explainer that adding ≠ creating a canonical, evidence drawer). **Deployed** (signal + artist-intel private, migration 003 live in prod; nquark-admin v5). **Prod validation**: intake "Arijit Singh" → `artist:arijit-singh` **WATCHING** (via existing MULTI_SOURCE promotion; demand-enrolled); intake "Anuv Jain" → honest **RESOLUTION_PENDING** (no canonical fabricated); YouTube identity flow deferred `QUOTA_EXHAUSTED` (real daily budget — not mock); unauth `/research/watchlist` **401** | artist-intel `models.py`+`watchlist.py`(new)+`yturl.py`(new)+`service.py`+`scheduler.py`+`signal_client.py`+`routes/watchlist.py`(new)+migration 003, signal `adapters/youtube.py`+`routes/youtube.py`+`schemas.py`, api-gateway `admin/watchlist.py`(new)+`routes/admin.py`+`config.py`, frontend `admin/watchlist.tsx`(new)+`api.ts`+`AdminApp.tsx`, `deploy/fly/admin-console.toml`, tests `test_watchlist.py`×2+`test_youtube.py`+`test_admin_watchlist.py` |
| Admin D.2 — authenticated production closure (deployed v4) | **Small operational closure of Admin D.** (1) **Gated `/v1/platform/status`** — it enumerated internal service names + health (production topology) and was publicly readable; now requires the read-only **VIEWER** principal (`require_viewer`): unauth **401**, admin-disabled **404**, local-mode open; public `/health` probe unchanged. (2) **Warm observatory** — `nquark-admin` keeps **one machine always on** (`min_machines_running=1`, `auto_stop=false`); **no collection-service config touched**. (3) **Deployed v4 + verified live over HTTPS**: SPA 200, `/v1/platform/status` unauth 401 (no topology leaked), `/health` 200, `/auth/status` production/sin/read-only; **restart proof** (that machine only) — gate holds, `/auth/login` 302→Google with the **corrected** client id, collection fleet untouched; the `fly deploy` re-proves the Admin Docker build. (4) OAuth `invalid_client` **CLOSED** (operator fixed the client id). Operator-side remaining = the actual `@clockwork-av.com` login + through-console live proofs (auth logic itself covered by the crypto regression suite). Gateway **106** green | api-gateway `main.py`, `deploy/fly/admin-console.toml`, `tests/{test_admin_oidc,test_health}.py`, `docs/{admin-console,deployment}.md` |
| Admin D.1 — OIDC security hardening + LIVE production deploy | **Closed the OIDC security gap and deployed the public console.** (1) **Cryptographic id_token verification** (was: unsigned local decode): `oidc.py` verifies the RS256 signature against Google's **JWKS** (cache-controlled, key-rotation refresh); PyJWT enforces `aud`/`exp`/required claims; `iss` checked; **`alg=none` + unknown-kid rejected**; a **`nonce`** is bound into the signed login state and re-checked; then `email_verified` + Workspace-domain allowlist (fail-closed). Dep: `pyjwt[crypto]`. (2) **Regression suite** (`test_oidc_verification.py`, real RSA key + injected JWKS): forged / unknown-key / unknown-kid / alg=none / wrong-iss / wrong-aud / expired / unverified-email / wrong-domain / hd-spoof / nonce-mismatch all rejected; valid accepted. (3) **Hard read-only** proven server-side (`test_admin_readonly.py`): reads reject writes (405); `/operations/*` 403 for VIEWER + 503 when disabled; governed mutations 403 for VIEWER. (4) **Env identity**: `/auth/{status,me}` report environment/region/read_only; SPA shows **PRODUCTION · READ ONLY · SIN**. (5) **Deployed `nquark-admin`** (public HTTPS, auto IPs, 2 HA machines, `sin`): HTTPS serves the SPA; `/auth/status` production/oidc; unauth `/dashboard` **401**; `/v1/platform/status` shows the **6 Flycast downstreams ok** (entity/analytics degrade — not deployed) = **reads live prod, not localhost**; no secrets in bundle/HTML/responses; session cookie **HttpOnly+Secure+SameSite=Lax** (stateless, survives restart); restart proof passed. **BLOCKER (operator-side)**: Google returns `Error 401: invalid_client` at the login redirect — the staged `NQUARK_OIDC_CLIENT_ID` doesn't match an existing OAuth client, so live human login can't complete until the operator corrects it (redirect URI + client id in Google Cloud) | api-gateway `admin/oidc.py`+`routes/admin.py`+`pyproject.toml`, frontend `admin/{auth,AdminApp,api}.tsx`, `deploy/fly/admin-console.toml` (live), `docs/{admin-console,deployment}.md`, `tests/{test_oidc_verification,test_admin_readonly,test_admin_oidc}.py` |
| Admin D — authenticated production console (`nquark-admin`) | **The console becomes the primary human interface for observing production** — no more SQL/curl/Fly logs for ordinary inspection. ONE public Fly app: the same `api-gateway` codebase serving the **built React SPA + `/admin/v1` read-only BFF from a single image**, reaching the private Flycast services, gated by **Google Workspace OIDC** (verified email in the allowed domain `clockwork-av.com`; deny-by-default; single read-only role; httpOnly signed session cookie), **operationally read-only**. Slotted OIDC into the pre-existing provider-neutral auth seam (`authenticate()→Principal`, server-side `require_role` now reads the session cookie or a Bearer token) — no auth rearchitecture; added `admin/oidc.py` (auth-code flow, server-to-server TLS token exchange, id_token claim validation, signed anti-CSRF state, same-origin-only redirect — no new deps). Gateway serves the SPA at `/` (assets via a `StaticFiles` mount) and adopted the fleet `DATABASE_URL`/`MIGRATION_DATABASE_URL` convention. **Full clarity frontend redesign** reusing the BFF read models: refined single dark design system (cool-ink neutrals + one indigo brand accent; upgraded shared primitives so reused screens restyle for free), status-aware auth gate + "Sign in with Google" screen + identity/sign-out, grouped-IA shell (Explore/Analyze/Collection/System) with global search, a new **production-state Overview**, and a new **bounded-inference Analysis** screen (deterministic aggregations — integrity as four separate components, supply×demand side-by-side never fused, no scores/prediction/causal claims). **Not yet deployed** — waits on the operator creating the Google OAuth client + setting 3 fly secrets; then `fly deploy` + allocate the public IP. Deploy boundary invariant updated: `ADMIN_LOCAL_MODE` never on any cloud manifest; the console manifest must be authenticated + read-only | api-gateway `admin/oidc.py` (new)+`admin/auth.py`+`routes/admin.py`+`main.py`+`config.py`+`alembic/env.py`, frontend `admin/{AdminApp,auth,api,ui,overview(new),analysis(new)}.tsx`+`index.css`, `deploy/fly/admin-console.{toml,Dockerfile}` (new), `docs/deployment.md`, `tests/{test_admin_oidc(new),test_admin_phase_c}.py` |
| 5A.3.3 — prod collection unblock (missing observation-service) | **Root cause of the empty prod DB found + fixed.** Prod accrued **0** canonical data despite an always-on collector reaching real Boshow/District (HTTP 200): signal-service's ticketing `/ingest` (the capture write path) HARD-depends on observation-service, but observation-service **was never deployed to Fly** — so every ingest hit `nquark-observation-service.flycast` (DNS-unresolvable) → **502** → the collector classified **285/309 events `SOURCE_UNAVAILABLE`**, 0 PRESENT → 0 entity candidates → 0 canonical artists → empty graph → demand had nothing. **Fix**: deployed observation-service to the private Flycast spine (5th app, region `sin`, no public IP, always-on min-1), attached to the **same** shared Managed Postgres (own `alembic_version_observation` table; migration over the direct endpoint). Adopted the fleet DB-URL convention (`DATABASE_URL` pooled + `MIGRATION_DATABASE_URL` direct, normalized to `postgresql+psycopg://`). Added a **readiness guard** — signal `GET /health/ready` returns **503** naming the reason when observation-service is unreachable (liveness/readiness split so a blip doesn't flap routing). Throttled the **demand→crawl request storm**: a process-wide short-TTL cache on `CrawlServiceClient.artists()` collapses a whole collector tick's backfill + per-candidate promotion + reconciliation into one `/entities` enumeration. **Prod proof (post-deploy)**: observations **0→221→294** (accruing ~70/min), graph **0/0 → 298 nodes/399 edges**, entity-resolution ARTIST rate **1.0**, signal `/health/ready` **200 ready** (observation reachable). No new paid cluster (reused shared PG); private-only invariant verified (`fly ips`) | `deploy/fly/observation-service.toml` (new), observation `config.py`+`alembic/env.py`+`fly.toml`, signal `main.py`+`clients/observation_client.py`, artist-intelligence `crawl_client.py`+`config.py`, `scripts/fly-{deploy,smoke}.sh`, `deploy/fly/README.md` |
| 5A.3.2 — canonical artist state reconciliation | Resolved the prod "backfill sees 0 canonical artists". **Root cause**: prod genuinely had no accrued canonical artists (1 tracked event, empty graph+registry) — not drift; the lone `artist:arijit-singh` demand rows are an **orphan** from earlier manual validation. Documented ownership: crawl entity-resolution registry owns canonical **identity**; graph is the **representation**. **Fix**: governance `create-artist` now also writes a RESOLVED registry row (closes a 5A.3.1 gap where promotion-created artists were graph-only + invisible to `/entities`); new idempotent `reconcile-graph-artists` registers pre-existing graph-only nodes (no node create/dup, no id rewrite). Extended `/demand/artist-universe` with a `canonical_reconciliation` block (registry vs graph vs demand-referenced counts + **orphan audit**, safe-degrade). Local proof: reconcile registered 6 graph-only artists → registry(84)==graph(84); 3 orphan demand refs audited (not rewritten). Deployed to private Fly | crawl `governance.py`+`routes/governance.py`+`enrichment/clients.py`, artist-intelligence `universe.py`+`graph_client.py`, `docs/demand-intelligence.md` |
| 5A.3.1 — candidate promotion & acquisition closure | Closed the four 5A.3 gaps (no redesign). **Candidate → canonical promotion** (`promotion.py`): deterministic policy MATCH_EXISTING_CANONICAL (link) / MULTI_SOURCE_CONFIRMED / INDIA_LIVE_EVIDENCE_PLUS_MUSIC_IDENTITY (create) — creation routed through a new crawl-owned `create-artist` governance endpoint (ownership stays in crawl/graph; demand service never writes canonical); weak single-source YouTube evidence never canonicalises; auto-enqueues identity resolution after link/create; bounded persisted backlog drain. **Bounded YouTube ecosystem discovery** (seed channels → one-hop uploads → candidates only). **Dynamic search-quota allocation**: per-purpose sub-accounting (SEARCH:unresolved/discovery/ambiguity), borrow unused when others idle / near reset, global reserve never borrowable, diagnostics expose configured/used/borrowed. **Live event-proximity cadence**: nearest upcoming Indian event read live from the graph (FEATURES), fed into cadence bands, safe-degrade on graph failure. Also fixed a latent 5A.3 defect (`crawl_client.artists` hit `/v1/internal/entities` 404 → silently empty). Deployed to private Fly | artist-intelligence `promotion.py`+`discovery.py`+`quota.py`+`scheduler.py`+`service.py`+`crawl_client.py`+`collector.py`, crawl `governance.py`+`routes/governance.py` (create-artist), `docs/demand-intelligence.md`, ADR-0018 |
| 5A.3 — Indian artist universe & demand saturation | Decoupled the artist universe from ticketing coverage + maximised irreplaceable temporal collection, **reusing** the 5A spine (signal-service = single acquisition path; artist-intelligence = stateful; entity/graph = canonical owner). Migration **002** (additive/reversible): **`artist_candidate`** ledger (discovery surfaces → candidates, never canonical; idempotent on `(source, source_id)`), **`artist_market_evidence`** (India evidence CLASSES `CONFIRMED_LIVE_INDIA`/`INDIA_DEMAND_OBSERVED`/`INDIA_MARKET_CANDIDATE`, provenance-bearing, not a score), **`youtube_video`** registry (separate from observations), **`provider_quota_bucket_day`** + job `priority`. **Auto-onboard + backfill** (event-derived + whole existing cohort, persisted/quota-managed — no manual op; collector runs them). **Independent YouTube discovery** (bounded config India queries → candidates). **Quota buckets** SEARCH/GENERAL_READ/VIDEO_STATS_BATCH (configurable fractions, provider-tz reset, 95%-target + reserve, defer-not-invalidate; search never on known-id refresh). **Identity-discovery queue** (search→channels.list verify→resolve, 5A.1a preserved). **One-time catalogue backfill** → registry; **hourly** live-metric observations (Trends daily); **adaptive + event-aware cadence**; **batch stats** primitive (`videos.list` 1u/50) in signal-service. Typed failure outcomes. Read-only artist-universe + quota-bucket diagnostics through the 5A.2 admin. No BMS dependency; no new public Fly surface | artist-intelligence `candidates.py`+`videos.py`+`discovery.py`+`cadence.py`+`universe.py`+`scheduler.py`+`service.py`+`quota.py`+migration 002, signal `adapters/youtube.py`+`routes/youtube.py` (batch), gateway `admin/demand.py`, frontend `admin/demand.tsx`, ADR-0018, `docs/{demand-intelligence,providers/youtube,providers/google-trends}.md` |
| 5A.2 — demand inspection surface | Exposed the Phase 5A demand read models through the existing **local-only inspection console** (observability, not a new model). New `artist_intelligence` gateway downstream (:8010) + **DemandAdminService** BFF (`/admin/v1/demand/*`: overview, summary, artist bundle, bounded observations, event context) — browser talks only to the gateway; every panel degrades to `available:false` (never 500). Frontend **Demand Intelligence** nav screen (coverage / YouTube **REAL-MOCK-UNKNOWN** mode / quota / read-only scheduler / Trends OFFICIAL_API-IMPORT), full **Artist Demand** view (identities w/ explicit verification + `last_verified_at`, YouTube state+deltas, independent momentum, relative-interest Trends, sortable geography, observed live supply, bounded observations), embedded as a section on the ARTIST entity page, an **Event → Demand context** tab (co-movement only), and a dashboard card. Two small read-only backend additions (no migration): `build_scheduler_state`, `youtube_provider_mode` (REAL/MOCK via signal `/health`), + bounded `reason`/`invalidation_reason` on identities. **No mutation controls**; **local-only** boundary preserved (no Fly manifest change) | api-gateway `admin/demand.py`+`routes/admin.py`, frontend `admin/demand.tsx`+`api.ts`, artist-intelligence `intelligence.py`+`routes/demand.py`, `docs/{admin-console,demand-intelligence}.md` |
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

## Phase 5A — public demand intelligence (2026-08-07, LIVE VALIDATED, real YouTube API + Trends import)

New **artist-intelligence-service** (port 8010) — the demand-side layer. Two evidence systems meet only
through `canonical_artist_id`; YouTube/Trends metrics never enter the event Shadow Ledger. See ADR-0017,
`docs/demand-intelligence.md`, `docs/providers/{youtube,google-trends}.md`.
- **Reuse, not a parallel path**: signal-service remains the sole YouTube ingestion path (extended with
  additive acquisition-only `GET /v1/signals/youtube/search` + `/channels/{id}/videos/preview`); the
  demand service's YouTube provider is a thin signal-service client. API key stays in signal-service.
  Kept in a **separate service** so demand-layer failure never disrupts the crawl→signal collection spine.
- **Demand ledger** (migration 001, `alembic_version_artist_intel`): `artist_external_identity`,
  `artist_demand_observation` (append-only, idempotent on `observation_key`, `evidence_status` +
  provenance), `provider_quota_day`, `demand_refresh_job`. Additive/reversible (upgrade+downgrade verified).
- **Identity**: deterministic resolution (bounded search → ranked candidates → RESOLVED/AMBIGUOUS/
  UNRESOLVED); name-equality alone never resolves; never creates a canonical artist; pending/rejected slots
  auditable.
- **Google Trends**: `OFFICIAL_API` (gated → reports `ACCESS_UNAVAILABLE`, no alpha creds) + `IMPORT`
  (labeled CSV exports; no scraping). Relative 0–100 interest with preserved normalization context;
  SEARCH_TERM vs TOPIC kept distinct; independently normalized exports never merged; ISO IN-XX geography.
- **Read models**: momentum (independent components, no score), geography (demand×supply, transparent
  labels), supply-demand juxtaposition (no combined score), event-response (co-movement only, no causal
  claim); `INSUFFICIENT_HISTORY` honesty; freshness; bounded/filterable observation listing.
- **Live demo** (docker, **real `YOUTUBE_API_KEY` in signal-service**): "Arijit Singh" name-only →
  **AMBIGUOUS** (multiple real channels, no corroboration — correct); with channel-id evidence →
  **RESOLVED** (score 1.0). Real snapshot: **3 channel + 15 video** observations; re-refresh **idempotent**
  (0 created). Quota accounted (**2 searches/200u + 6 reads/8u**). Trends import: **5** West Bengal-led
  REGION observations (`IMPORTED_PROVIDER_EXPORT`, normalization context, IN-WB). Momentum honestly
  `INSUFFICIENT_HISTORY` (1 snapshot); geography label `HIGHER_OBSERVED_DEMAND / LOWER_OBSERVED_SUPPLY`
  (demand 100, supply 0 — Arijit not in the captured indie inventory); provider-health `ACCESS_UNAVAILABLE`;
  event-response `EVENT_NOT_FOUND` (honest). Coverage carries the "NOT complete market demand coverage"
  disclaimer.
- **Fly**: optional private Flycast `deploy/fly/artist-intelligence-service.toml` — **deliberately not in
  `fly-deploy.sh`** (hand-deploy only; can't disrupt the collection spine). No paid resources created.

## Phase 5A.1a — YouTube identity verification integrity hotfix (2026-08-08, LIVE VALIDATED)

Production defect: a search-result candidate could transition a YouTube CHANNEL_ID to `RESOLVED`
(confidence 0.8, `last_verified_at` stamped) even though an authoritative `channels.list(id=…)` returned
`items: []` — i.e. the channel did not exist (real case: `artist:arijit-singh` →
`UCUEcefFC0sBRZfCTBqcx9jg`). Search evidence alone was resolving identities.
- **Invariant now enforced**: a CHANNEL_ID may become `RESOLVED` only after an authoritative
  `channels.list` lookup confirms that exact id exists at resolution time; **search is candidate
  discovery only**. `last_verified_at` is populated **only** after a successful provider verification.
- **signal-service**: additive `verify_channel` + `GET /v1/signals/youtube/channels/{id}/verify` →
  typed `FOUND` / `CHANNEL_NOT_FOUND` (empty `items` = NOT_FOUND, 200); provider/network failure → 502
  (never NOT_FOUND). Key stays in signal-service; no YouTube HTTP in artist-intelligence-service.
- **Resolution**: verify the deterministic leader; on `CHANNEL_NOT_FOUND` record rejection evidence and
  consider the next ranked candidate (which must independently satisfy the thresholds + verify); no
  candidate satisfying both → AMBIGUOUS/UNRESOLVED; transient verify failure → not resolved, nothing
  invalidated. Also fixed a float-margin bug in `_decide` (1.0−0.8 < 0.2) that mislabeled a clear leader.
- **Refresh**: verifies the id before writing; `CHANNEL_NOT_FOUND` → no observations (no fabricated
  zeros) + identity `UNRESOLVED` (`invalidation_reason=PROVIDER_ID_NOT_FOUND`); scheduler enqueues only
  RESOLVED identities so an invalidated id leaves recurring refresh; `VERIFICATION_UNAVAILABLE`
  (transient) is retried, never invalidates. No new status enum; no migration.
- **Live** (docker, real `YOUTUBE_API_KEY`): the production id `UCUEcefFC0sBRZfCTBqcx9jg` verifies
  `CHANNEL_NOT_FOUND` → not resolved; a real valid channel verifies `FOUND` → RESOLVED with
  `last_verified_at`; refresh of the stale production row invalidates it and writes nothing. Regression
  suite +9 (`test_verification.py`); demand **43**, signal **99** (verify primitive), all green.

## Phase 5B.2 increment 2 — Market Observatory UX (2026-08-12, DEPLOYED to Fly)

Made n-quark understandable to a non-developer operator. Backend ontology unchanged; the frontend stops
exposing it as the primary mental model.

- **Product-facing entity contract**: normal Artists/Venues screens are sourced ONLY from the authoritative
  canonical registry (crawl entity-resolution), **never raw graph artist-type nodes** — so the 38
  `boshow:artist:*` source projections (see the §0 preflight) can never inflate the product Artist count
  (which stays 64, not 102). Source projections live under Evidence/Advanced.
- **Catalog BFF** (gateway `admin/catalog.py`): `/admin/v1/catalog/{artists,venues}` — canonical rows from
  crawl, enriched with monitoring state (watch status, YouTube identity state, owned videos, demand,
  moving count) from artist-intelligence in **one batch call** (`/v1/internal/artists/summaries`) — no
  per-row N+1. Degrades: identity + live activity still render if artist-intelligence is down.
- **Frontend `product.tsx`**: first-class **Artists** (list + filters: watching / YouTube verified / needs
  identity / has events / has demand / moving) → **Artist detail** with a prominent **"What n-quark knows"
  Data Coverage** (Identity / Live activity / YouTube / Demand / Evidence) that distinguishes
  **ZERO_OBSERVED / NOT_COLLECTED / UNAVAILABLE / INSUFFICIENT_HISTORY** in plain language (never a bare 0);
  content-movement cards with evidence; first-class **Venues** (list + detail); **Market Movement** with an
  honest zero-data empty state linking to the Watchlist. No fused score, no ontology as primary UI.
- **Navigation redesign**: Explore (Events · Artists · Venues · Organizers) · Monitor (Watchlist · Market
  Movement · Demand) · Coverage (Collection · Captures) · Advanced (Analysis · Resolution · Graph ·
  Diagnostics · All entities · System). Artists/Venues are dedicated sidebar items; the generic Entities
  screen is demoted to Advanced. Watchlist links resolved targets straight to Artist detail.
- **Deployed + validated**: artist-intel (summaries) + nquark-admin (BFF + new bundle). Gate holds
  (`/catalog/*`, `/market/movement` → 401 unauth; SPA 200); summaries endpoint live (62 monitored artists);
  Artists list sources the 64-row canonical registry (not 102 graph nodes) — by construction + tests + the
  §0 preflight. Market Movement shows the honest zero-content state (prod has 0 verified channels).
- Tests: gateway **125** (+6 catalog: canonical-only / no graph inflation / monitoring merge / filter /
  degraded / read-only), artist-intel **121** (+2 summaries + coverage). Frontend tsc + build + lint clean.

## Phase 5B.2 — YouTube content sensing + market UX (2026-08-12, INCREMENT 1 DEPLOYED; phase in progress)

Turning periodic artist/channel metrics into continuous content sensing + a market-intelligence UX. Being
built in tested increments. **Increment 1 (deployed)** = the deterministic content-movement backend + the
read models the UX depends on. **Remaining** (next increments, not started/partial): ecosystem discovery
(§11–14), movement-aware cadence (§6), and the frontend redesign (§19–32) + UI proof (§35).

- **§0 Canonical preflight (done, read-only)**: resolved the "64 vs 102" ambiguity. The **102 is NOT
  comparable to the canonical count** — it is total *artist-type graph nodes*: 64 canonical ARTIST nodes
  (`artist:<slug>`, registry-backed) **plus 38 source-handle projection nodes** (`boshow:artist:<…>`,
  `origin=null`, only display_name+updated_at). The 38 are raw Boshow source-artist handles not yet folded
  into a canonical — many are **compound/ambiguous** ("Sumeli Chakraborty / Atulita Banerjee",
  "Kabir Suman . Gangur"), organisations ("…Cultural & Welfare Society"), or generic singles. `registry_only`
  = 0 (every canonical has a graph node). **No graph-only *canonical* cohort exists**, so `reconcile_graph_artists`
  is **not** appropriate here (it would fabricate canonicals from ambiguous source strings — forbidden);
  these resolve through normal entity resolution over time. No mutation, no new registry, no id rewrite.
- **Content registry semantics (§3/4/15)**: migration 004 (additive/reversible) adds to `youtube_video`:
  `relationship_type` (OWNED_CONTENT = published by the artist's verified channel — ownership only, not a
  relevance claim; vs ECOSYSTEM_CONTENT), `availability_state` (AVAILABLE/UNAVAILABLE/NOT_FOUND — a KNOWN
  video's provider availability; a transient failure never becomes NOT_FOUND; history never deleted), and
  `discovery_method`. Per-video statistics time-series already lives in `artist_demand_observation`
  (scope=CONTENT), reused unchanged.
- **Movement engine (§6–10, the core)** `movement.py`: deterministic, **age-normalised**, **evidence-gated**
  content movement computed over the existing per-video observation series (collects nothing). Windowed
  velocity + acceleration; a comparable-age baseline (a video judged only against same-age-bucket siblings,
  self excluded by id); states **INSUFFICIENT_HISTORY / NORMAL / RISING / BREAKOUT_CANDIDATE / COOLING**,
  each carrying a full **evidence contract** (cohort, observation count, baseline sample size, metrics,
  thresholds, supporting values, evidence_state). **No fused virality score.** Thresholds are config.
  `artist_movement` + `market_movement` aggregate from constituent evidence (owned vs ecosystem moving,
  independent active channels, cross-channel flag) — never one number.
- **Artist Data Coverage (§18/19 backend)** `coverage.py`: "what does n-quark know about this artist" —
  identity / live activity / youtube / demand / evidence — explicitly distinguishing **COLLECTED /
  ZERO_OBSERVED / NOT_COLLECTED / UNAVAILABLE / INSUFFICIENT_HISTORY** (§32).
- **Surfaces**: artist-intel `/artists/{id}/movement`, `/artists/{id}/coverage`, `/market/movement`;
  gateway BFF passthrough (`/admin/v1/demand/artists/{id}/{movement,coverage}`, `/admin/v1/market/movement`),
  VIEWER read-only, degrades gracefully.
- **Deployed + prod-validated (§34, honest)**: migration 004 live; the engine runs. Prod currently has
  **0 verified YouTube channels / 0 video-registry rows** (every real channel resolution has returned
  CHANNEL_NOT_FOUND/AMBIGUOUS under real channels.list + daily-search limits — e.g. Arijit's stale channel),
  so content-sensing has **no input yet** and the read models honestly report `NOT_COLLECTED` /
  `ZERO_OBSERVED` / `UNAVAILABLE` — nothing fabricated, no timestamps manipulated. The engine's behaviour is
  proven by the deterministic test suite; end-to-end owned-content→movement needs a verified channel (an
  operator can paste a known-correct channel URL via the watchlist to seed it).
- Tests: artist-intel **119** (+13 movement/coverage; migration 001↔004 round-trip), gateway **119** (+3).

## Phase 5B.1.1 — watchlist canonical-integrity closure (2026-08-12, DEPLOYED to Fly)

Small integrity-closure pass. Explains the 5B.1 production report's apparent conflict and enforces a hard
canonical-reference invariant. No UI redesign; canonical ownership model unchanged (still crawl-owned).

- **Diagnosis (read-only, prod)**: the 5B.1 report said watch target "Arijit Singh" → `artist:arijit-singh`
  WATCHING via MULTI_SOURCE_CONFIRMED but "not in the crawl registry." Root cause = **stale/timing
  reporting artifact**: the `preview` step ran *before* resolution and correctly said NEW; the MULTI_SOURCE
  promotion then called `crawl.create_artist`, which **registers** the canonical via
  `_register_external_artist` (5A.3.2). So immediately after resolution `artist:arijit-singh` was
  registry-backed. Current prod: **present in the crawl registry (64), graph (102), and the watch target /
  candidate / demand ledger — 0 orphans anywhere**. Not a promotion bug, not a create_artist regression,
  not a real orphan.
- **Canonical-reference invariant (the fix)**: a watch target may expose a `canonical_artist_id` (and be
  WATCHING) only if the authoritative crawl registry acknowledges that id. New
  `crawl_client.canonical_artist_registered(cid)` does an **uncached, by-id** check against
  `/v1/internal/entity-resolution/entities/ARTIST/{id}` (a graph-only node with no registry row → 404 →
  False; a provider outage raises → caller fails closed). `promotion.promote` now **re-reads/confirms** the
  resolved id against the registry before `link_to_canonical` / market-evidence / identity enqueue — so a
  **stale `candidate.canonical_artist_id` (ALREADY_LINKED) is never trusted** (→ `promoted=False`,
  `CANONICAL_UNVERIFIED`); this closes the vector for every caller (watchlist, discovery backlog, universe).
  `watchlist.resolve_target` additionally verifies an **operator-selected** canonical before onboarding;
  an unbacked id is never exposed — the target stays RESOLUTION_PENDING with `detail.canonical_unverified`
  (auditable), never WATCHING. `create_artist`'s own registration remains the only sanctioned reconciliation
  (never a silent registry write from an orphan).
- **Provider vs canonical stay distinct**: the demand ledger keys on `canonical_artist_id`, so provider-only
  monitoring cannot exist without a canonical; rather than invent a parallel namespace or a fake canonical,
  a hint-only target stays honestly pending. Preview and resolve now share the same authoritative registry
  semantics (preview may say NEW and resolve later MATCH when evidence improves — legitimate).
- **Auditability**: new `watchlist.canonical_integrity` + `/research/watchlist/canonical-integrity`
  (+ gateway BFF, VIEWER) reports orphan canonical references across watch targets / candidates / external
  identities / demand observations. No bulk auto-rewrite.
- **Deployed + prod-proved** (artist-intel + nquark-admin; signal/crawl untouched): `canonical_artist_registered`
  returns **True** for `artist:arijit-singh`, **False** for a nonexistent id (fail-closed); `canonical_integrity`
  = **0 orphans** (registry 64); the **URL-hint path is verification-gated** — real forHandle + channels.list
  (REAL mode) returned NOT_FOUND for `@arijitsingh` and for a stale ledger channel id, and the system
  **refused to resolve or fabricate** any identity (no fabricated provider id used). Unauth
  `/research/watchlist/canonical-integrity` → 401.

## Phase 5B.1 — artist intake & research watchlists (2026-08-12, DEPLOYED to Fly)

First slice of Phase 5B (Market Coverage & Sensing). Makes it trivial for an authenticated operator to
say "start watching this artist" — no canonical id, YouTube channel id, SQL, curl, or provider knowledge.

- **Watch target (not a canonical artist)**: `artist_watch_target` (migration 003, additive/reversible)
  is a durable operator research instruction with its own lifecycle (NEW · RESOLUTION_PENDING · WATCHING ·
  AMBIGUOUS · REJECTED · PAUSED). Owned by artist-intelligence-service alongside the existing candidate /
  identity architecture — **no second artist identity system**.
- **Reuses the existing machinery**: `watchlist.py` intake is idempotent (dedup canonical›channel›name);
  resolution links to an existing canonical (deterministic name match) or promotes through the EXISTING
  `promotion` rules. **An operator instruction is a single independent discovery source**, so a lone name
  with no existing canonical stays RESOLUTION_PENDING — canonical safety is unchanged, nothing is
  fabricated. Resolved targets flow through `universe.onboard_artist` into the existing demand scheduler.
- **YouTube hint = hint, not proof**: `yturl.py` parses a pasted channel id / `@handle` / video URL. A
  handle/video is mapped to a channel id via NEW signal-service acquisition-only lookups
  (`resolve/handle` = channels.list `forHandle`; `resolve/video` = videos.list `snippet.channelId`), then
  the channel is confirmed by the **authoritative channels.list existence check**
  (`service.resolve_youtube_from_hint`) before an identity becomes RESOLVED. External YouTube HTTP stays
  in signal-service (the single ingestion path).
- **Pause/resume**: a PAUSED target suspends recurring collection for that artist (the scheduler's
  `enqueue_due` skips paused canonical ids) without deleting any history; resume restores it.
- **Controlled write boundary**: gateway `/admin/v1/research/watchlist` is the ONE narrow authenticated
  write (RESEARCH CONFIGURATION) — structurally separate from the canonical/admin mutation routes,
  flag-gated (`ADMIN_RESEARCH_CONFIG_ENABLED=true` on the console), authenticated (any Workspace VIEWER),
  records `created_by` = the OIDC principal, audited. Canonical entities / observations / graph nodes /
  provider observations / resolution outcomes / event & historical state stay **read-only** from here.
- **Frontend**: an **Artists · Watchlist** screen — add one, paste a YouTube URL, or bulk-add; targets
  grouped by human-readable state (Watching / Finding YouTube identity / Needs review / Waiting for
  stronger evidence / Paused); a prominent explainer that adding does not create a canonical artist; an
  Evidence drawer exposing the raw lifecycle enum + resolution method; live diagnostics.
- **Deployed + prod-validated** (signal-service + artist-intelligence-service private Flycast; **migration
  003 live in prod Postgres**; nquark-admin v5). Against real production data: bulk preview (read-only)
  ran against the live crawl registry; intake **"Arijit Singh" → `artist:arijit-singh` WATCHING** (via the
  existing MULTI_SOURCE_CONFIRMED promotion; already demand-enrolled), YouTube identity flow deferred
  **QUOTA_EXHAUSTED** (real daily search budget — proof of genuine rate discipline, not mock); intake
  **"Anuv Jain" → RESOLUTION_PENDING** (honest — no canonical fabricated from a single operator source);
  `created_by` recorded; unauthenticated `/admin/v1/research/watchlist` → **401**. Collection spine
  (crawl/graph/observation/media) untouched; nquark-admin kept one machine warm (D.2).

## Admin D.2 — authenticated production closure (2026-08-12, DEPLOYED to Fly, v4)

Small operational closure of Admin D (no redesign, no new features). Deployed + verified live.

- **Gated `/v1/platform/status`**: the aggregate status enumerated every internal service name + health
  (production topology) and was **publicly readable**. It now requires the same read-only **VIEWER**
  principal as the rest of the console (`Depends(require_viewer)` in `main.py`). Unauthenticated → **401**
  (production/OIDC), **404** when the admin API is disabled entirely, and local mode short-circuits to the
  internal context. The public **`/health`** load-balancer probe is unchanged. Regression tests
  (`test_admin_oidc.py`): unauth 401 (no `services` leaked) / authenticated viewer 200 / admin-disabled 404
  / local-mode open; the pre-existing aggregation test now runs under local mode. Gateway **106** green.
- **Warm observatory**: `nquark-admin` now keeps **one machine always on**
  (`min_machines_running=1`, `auto_stop_machines=false`) so the console answers with no cold start. **No
  collection-service machine config changed.** Verified: one machine `started` (health passing), one
  `stopped` (auto_start on demand / HA headroom).
- **Live deploy (v4) + verification**: `fly deploy` (same admin-console toml/Dockerfile — the Docker build
  passes as part of the deploy). Over HTTPS `https://nquark-admin.fly.dev`: `GET /` SPA **200**;
  `/v1/platform/status` unauth **401** (`{"detail":"authentication required"}`, no topology); `/health`
  **200**; `/admin/v1/auth/status` `production / region sin / read_only true`. **Restart proof**
  (`fly apps restart nquark-admin`, that machine only): SPA 200, gate still **401**, `/auth/login` **302 →
  Google** with the **corrected** client id `637599001088-…` + registered callback; collection fleet
  untouched by construction.
- **OAuth `invalid_client` blocker CLOSED**: the operator corrected `NQUARK_OIDC_CLIENT_ID` (was missing
  its leading digit). Google now serves its real sign-in, domain-locked to `clockwork-av.com`.
- **Operator-side remaining (credential-gated, cannot be automated by the agent)**: the actual
  `@clockwork-av.com` Google login + through-console live reconciliation/logout/unauthorized-account proofs.
  The **auth logic itself** is covered by the cryptographic regression suite (forged/unknown-key/alg=none/
  wrong-iss/wrong-aud/expired/unverified/wrong-domain/nonce-mismatch rejected; valid accepted; writes
  refused server-side; logout clears the cookie), and production ground truth by direct private reads
  (see D.1). No secrets/tokens are exposed to the browser (bundle/HTML/storage/responses clean).

## Admin D.1 — OIDC security hardening + LIVE production deploy (2026-08-12, DEPLOYED to Fly)

Focused production-security + deployment closure (no new features, no redesign).

- **Cryptographic verification (the key fix)**: Admin D decoded the id_token without checking its
  signature (trusting the TLS channel). `oidc.py` now **verifies RS256 against Google's JWKS**
  (`oauth2/v3/certs`, cache-controlled with a one-shot rotation refresh); PyJWT enforces `aud` (our client
  id), `exp`, and required claims; `iss` ∈ Google's issuers; **`alg=none` and unknown key ids rejected**.
  A random **`nonce`** is bound into the signed login `state` and re-checked against the token (replay/CSRF
  binding). `email_verified` + the Workspace-domain allowlist still apply (fail-closed; `hd` must
  corroborate). New dependency `pyjwt[crypto]` (no hand-rolled crypto). State/CSRF unchanged.
- **Tests**: `test_oidc_verification.py` (real RSA keypair + injected JWKS, no network) proves forged /
  unknown-key / unknown-kid / alg=none / wrong-iss / wrong-aud / expired / unverified-email / wrong-domain
  / hd-spoof / nonce-mismatch are all rejected and a valid Workspace identity is accepted;
  `test_admin_readonly.py` proves writes are refused server-side; new session-cookie-flags + expired-state
  tests. Gateway **84 → 102**.
- **Live deployment** (`fly deploy` from the admin-console toml/Dockerfile): `nquark-admin` public over
  HTTPS (`https://nquark-admin.fly.dev`), region `sin`, Fly auto-provisioned the public IPs (shared v4 +
  dedicated v6), 2 HA machines 1/1. Verified live: SPA over HTTPS + assets; `/auth/status`
  `environment=production region=sin read_only=true`; `/auth/login` 302 → Google with the real client id +
  correct callback + signed state/nonce; unauth `/admin/v1/dashboard` **401**; `/v1/platform/status` = the
  **6 required Flycast services `ok`** (entity/analytics `error` — not deployed, degrade); **no localhost**.
  Bundle/HTML/responses carry **no** client secret / token / signing secret / Flycast hostname. Session
  cookie **HttpOnly+Secure+SameSite=Lax**, stateless (survives the `fly apps restart` proof; collection
  fleet untouched). **PRODUCTION · READ ONLY · SIN** badge visible on both the login and app.
- **Production-data reconciliation** (direct private reads vs. what the BFF computes from the same hosts):
  prod graph **1633 nodes / 2453 edges**, observations **7743** (both accruing during the session), entity
  resolution **58 unique canonical artists** (rate 0.948) / **337 venues**, signal youtube **REAL** +
  `/health/ready` ok, artist-intelligence healthy — **materially different from the local Docker dataset**
  (238 events / 84 artists / 550 nodes), confirming the console reads **live production**. Inference proof:
  the artist-universe diagnostic returns bounded OBSERVED/DERIVED/AUDIT metrics with honest
  insufficient-evidence (India presence 0) and the "not complete market coverage" disclaimer — no
  causal/predictive claim.
- **OPEN BLOCKER (operator-side, not code)**: at the Google redirect, Google returns **`Error 401:
  invalid_client` — "The OAuth client was not found"** for the staged `NQUARK_OIDC_CLIENT_ID`. The request
  is correctly formed (Google received + rejected the *client*), so the operator must point
  `NQUARK_OIDC_CLIENT_ID`/`NQUARK_OIDC_CLIENT_SECRET` at a real OAuth 2.0 Web client (with the exact
  callback registered). Until then, **live human login, and the through-console authenticated reconciliation
  / freshness / unauthorized-account live proofs, cannot complete** — these are covered cryptographically in
  automated tests and by direct-read ground truth.

## Admin D — authenticated production console (2026-08-11, BUILT + LOCALLY VERIFIED; deploy pending operator secrets)

The deployed console becomes the **primary human interface for observing n-quark** — the user should not
need SQL, curl, or Fly logs for ordinary product/data inspection. Design decisions (confirmed with the
operator): **Google Workspace domain sign-in** (`clockwork-av.com`), **single read-only role**, **full
clarity redesign**, **build-now / deploy-when-secrets-set**.

- **Architecture (one public app)**: `nquark-admin` = the same `api-gateway` codebase with the built React
  SPA baked into the image (`deploy/fly/admin-console.Dockerfile`, repo-root context: node build stage →
  gateway image with `frontend_dist`). FastAPI serves the SPA at `/` (`_frontend_index`/`root()`; assets via
  a `StaticFiles` mount added last so every API route wins) and the `/admin/v1` BFF same-origin (no CORS).
  Reaches the private services over `.flycast` (crawl/signal/observation/graph/media/artist-intelligence
  overrides). `force_https`, operationally read-only (`ADMIN_OPERATIONAL_ACTIONS_ENABLED=false`).
- **Auth (slots into the existing seam, no rearchitecture)**: `admin/oidc.py` implements the Google
  authorization-code flow with **no new dependency** — server-to-server TLS token exchange, then id_token
  claim validation (`iss`/`aud`/`exp`/`email_verified`) with a signed anti-CSRF `state` and same-origin-only
  `next`. `auth.email_is_allowed` gates on the **verified email domain** (fail-closed; `hd` must corroborate;
  optional extra allowlist). `require_role` now reads the **httpOnly signed session cookie** (browser) or a
  Bearer token (dev/tests); all authenticated users get `VIEWER`. Endpoints: `/auth/status` (public entry),
  `/auth/login` (302→Google), `/auth/callback` (mint cookie), `/auth/logout`. Gateway adopts the fleet
  `DATABASE_URL`/`MIGRATION_DATABASE_URL` convention so admin migrations target the attached Managed Postgres.
- **Frontend redesign (reuses BFF read models; do-not-port mandate honored)**: a single deliberate dark
  design system (`index.css` `@theme` cool-ink neutral ramp app-wide + one indigo brand accent, status hues
  kept separate; upgraded shared primitives in `ui.tsx` — so reused `screens/detail/demand` restyle for
  free — plus new StatTile/Bar/Sparkline/Section/KeyValue/Tag). New **status-aware auth gate** + "Sign in
  with Google" screen (`auth.tsx`); **grouped-IA shell** (`AdminApp.tsx`: Explore/Analyze/Collection/System,
  global search with `/` focus, identity + sign-out). New **Overview** (`overview.tsx`) = production-state
  landing (collection-active banner, KPI tiles, per-source table, demand coverage, service strip, attention
  queues). New **Analysis** (`analysis.tsx`) = bounded deterministic aggregation honoring the invariants
  (collection integrity as **four separate components, never one score**; **supply × demand side-by-side,
  never fused**; discovery contribution; coverage-gap backlog; honest disclaimer).
- **Local verification**: gateway tests **84** (+14: OIDC allowlist/state/status/login/cookie/callback +
  rewritten deploy-boundary). Frontend `tsc -b` clean, `vite build` ok, `npm run lint` exit 0. Single-app
  serving smoke-tested (SPA at `/` text/html, assets 200, `/auth/status` disabled/local, `/auth/me` 401 w/o
  session). In-browser against a local gateway: the login screen (disabled-mode notice), and in local mode
  the **redesigned shell + Overview + Analysis + reused Events** all render with live data.
- **Deploy handoff (pending)**: operator creates a Google OAuth web client (redirect
  `https://nquark-admin.fly.dev/admin/v1/auth/callback`), `fly apps create nquark-admin` + `fly mpg attach`,
  sets 3 secrets (`OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `ADMIN_SESSION_SECRET`), then `fly deploy` (the
  admin-console toml/Dockerfile) + `fly ips allocate`. See `docs/deployment.md`.

## Phase 5A.3.3 — prod collection unblock: the missing observation-service (2026-08-11, LIVE VALIDATED on Fly)

**The definitive answer to "prod DB has nothing and is not accruing."** Not a flags/egress/provider problem
(all disproven): the always-on prod collector *was* running and reaching real Boshow/District at HTTP 200,
with 309 tracked events and 24.8k capture jobs.

- **Root cause**: signal-service's ticketing `/ingest` (the capture write path) does `append_observations()`
  to observation-service as a **HARD dependency** (raises 502 on failure; entity-service + graph projection
  below it are best-effort). **observation-service was never deployed to Fly** — the Phase 4D deploy stood up
  only graph/signal/media/crawl. So signal's write to `http://nquark-observation-service.flycast` failed DNS
  (`[Errno -2] Name or service not known`) → **HTTP 502** on every capture ingest → the collector classified
  **285/309 `SOURCE_UNAVAILABLE`**, **0 `SUCCESS_RECORD_PRESENT`** (the 24 `RECORD_ABSENT` are genuine 404s) →
  **0** entity-resolution candidates → **0** canonical artists → graph **0/0** → demand universe empty. Local
  worked only because docker-compose runs the full service set including observation-service. Confirmed by a
  one-shot prod ingest returning `502 "Observation service write failed: [Errno -2] …"` and the Fly app list
  showing observation-service absent.
- **Fix — deploy observation-service to the private spine** (5th Flycast app, region `sin`, **no public IP**,
  always-on `min_machines_running=1` / `auto_stop=off` because it's a hard dependency of the capture path).
  New canonical `deploy/fly/observation-service.toml`; wired into `scripts/fly-deploy.sh` in dependency order
  (graph → **observation** → signal → media → crawl). **Reused the existing shared Managed Postgres**
  (`nquark-postgres`) via `fly mpg attach` — **no new paid cluster**; observation coexists in the one DB with
  its own `alembic_version_observation` table. Adopted the fleet DB-URL convention in observation
  (`normalize_db_url`, `DATABASE_URL` pooled runtime + `MIGRATION_DATABASE_URL` direct for Alembic DDL over
  pgbouncer being unreliable). Migration ran on boot (`Running upgrade -> 001`).
- **Health guard (surfaces this class of failure loudly)**: signal `GET /health/ready` probes the
  observation-service dependency and returns **503** with the failure detail + `required_by` when unreachable,
  kept separate from `/health` (liveness) so a transient blip doesn't flap health-gated routing. Added to
  `fly-smoke.sh`.
- **Demand→crawl request-storm throttle**: one collector tick fanned out backfill + per-candidate
  `find_artist_by_name` + reconciliation, each independently re-paging `/entities` — a burst per tick. Added a
  process-wide short-TTL cache (`crawl_artists_cache_ttl_seconds`, default 60s) on `CrawlServiceClient.artists()`
  so a tick shares a single enumeration (3 new unit tests prove the collapse; live: new machine 1 call/tick vs
  old machine's burst).
- **Prod proof (post-deploy, read-only over Flycast)**: observation total **0 → 221 → 294** (accruing
  ~70/min); graph `/v1/graph/stats` **0/0 → 298 nodes / 399 edges**; crawl entity-resolution coverage ARTIST
  **resolution_rate 1.0** (25 mentions, VENUE 74); signal `/health/ready` **200 `ready`**
  (`observation_service.reachable:true`); observation machine **1/1** checks, **private ingress IP only**
  (invariant verified via `fly ips`). Tests: signal **103** (+2 readiness), artist-intelligence **87** (+3 cache).

## Phase 5A.3.2 — canonical artist state reconciliation (2026-08-09, LIVE VALIDATED, docker)

crawl + artist-intelligence rebuilt/recreated. Live:
- **Ownership traced**: `/entities` reads crawl's `EntityResolutionCandidate` registry; graph holds the
  artist-node representation. Prod evidence gathered via Flycast: prod graph **0 nodes/0 edges**, registry
  **0 artists**, 1 tracked event — so prod's "0 canonical artists" is genuine (explanation #4), and
  `artist:arijit-singh` (9 demand obs, now UNRESOLVED) is an orphan from manual validation.
- **Registry write on create**: `create-artist` for "Prateek Kuhad …" → `registry_registered:true`;
  `/entities` count 77→78 and includes it → artist-intelligence backfill now **examines 78** (queued 1).
- **reconcile-graph-artists**: examined 84 graph ARTIST nodes, **registered 6** graph-only ones
  (idempotent; a second pass registers 0) → **registry (84) == graph (84)** converged.
- **Diagnostics**: `/demand/artist-universe.canonical_reconciliation` shows registry 84, graph 84,
  demand-identity refs 30, **3 orphan** demand refs (`__hotfix-stale-demo`, `arijit-singh`,
  `test-newcomer-5a3`) — audited, never rewritten.

**Deployed to private Fly** (crawl + artist-intelligence; both 1/1 passing). **Prod post-fix**: registry
**0** == graph **0** (genuine — prod has not accrued canonical artists; 1 tracked event; not fabricated),
`reconcile-graph-artists` safe no-op (examined 0), and the orphan audit now surfaces **1 orphan
(`artist:arijit-singh`)** in prod for the operator. Collection healthy (`youtube_mock=false`). The
create-artist→registry + reconcile machinery is proven locally (registry==graph converged, 6 registered)
and by tests, ready for when prod collection produces canonical artists.

## Phase 5A.3.1 — candidate promotion & acquisition closure (2026-08-09, LIVE VALIDATED, docker + real API)

crawl + artist-intelligence rebuilt/recreated with promotion/ecosystem/event flags on. Live:
- **Crawl-owned create-artist**: `POST /v1/internal/governance/create-artist` created `artist:<slug>` in
  crawl/graph — ownership stays outside the demand service.
- **Match promotion**: candidate "Ishani Nag" → **MATCH_EXISTING_CANONICAL** → `artist:ishani-nag`,
  identity discovery auto-queued (link, no create).
- **Weak evidence**: bounded backlog pass evaluated 10 YouTube-only single-source candidates → **0
  promoted** (correctly remain unresolved — no false canonicalisation).
- **Backfill fixed**: after the `crawl_client.artists` endpoint fix, backfill examined **56** real
  canonical artists (queued 5, bounded) — 5A.3's backfill had silently been a no-op (404).
- **Dynamic allocation**: `/demand/quota-buckets` → per-purpose search allocation operational
  (discovery 300/1400 used, unresolved 0/1400, ambiguity 0/525) with configured fractions shown separately.
- **Event-aware cadence** flag on; scheduler reads graph FEATURES live (safe-degrade to normal on failure).

**Deployed to private Fly** (2026-08-09): signal + crawl (create-artist) + artist-intelligence redeployed
(region sin, private Flycast, 1/1 checks passing each). **Migration 002 live in prod**
(`alembic_version_artist_intel=002_artist_universe`); `youtube_mock=false`; scheduler + discovery +
auto-onboard + promotion + event-aware flags all on in the artist-intel Fly toml. **Production proof**:
discovery producing candidates (**20**, all `YOUTUBE_SEARCH`, correctly `RESOLUTION_PENDING`); promotion
backlog evaluated 5 → **0 promoted** (weak single-source YouTube evidence correctly never canonicalises);
per-purpose search allocation consuming (discovery 200/1400); scheduler 2 SUCCEEDED + 1 FAILED_TERMINAL
(a stale-id invalidation — 5A.1a) persisted across the deploy restart; 9 demand observations retained.
Honest prod note: the prod crawl currently exposes **0 canonical ARTIST** entities (entity resolution
enabled but no canonical artists accrued yet), so backfill/match have nothing to act on in prod today —
the endpoint fix is verified locally against 56 canonical artists; discovery independence is what is
proven live.

## Phase 5A.3 — Indian artist universe & demand saturation (2026-08-09, LIVE VALIDATED, real YouTube API)

signal + artist-intelligence + api-gateway rebuilt/recreated; **migration 002 applied on boot** (all 4
new tables present, `alembic_version_artist_intel=002_artist_universe`). Real API (`youtube_mock=false`):
- **Independent discovery**: 1 config query ("Indian indie artist live") → **6 candidates created** (+4
  merged on rerun — idempotent, no explosion); all `YOUTUBE_SEARCH`, status NEW, **0 canonical artists /
  0 identities created** (candidates never pollute the graph).
- **Auto-onboard**: `artist:arijit-singh` (already RESOLVED) → identity_present, no job, CONFIRMED_LIVE_INDIA
  evidence recorded. A **new** artist `artist:test-newcomer-5a3` (Prateek Kuhad) → identity job queued →
  scheduler ran real **search + channels.list** → **AMBIGUOUS** (`last_verified_at` false — name-only
  matched multiple real channels; correctly NOT auto-resolved, stays non-canonical). 5A.1a preserved.
- **Catalogue backfill** (real): **15 uploads registered** into `youtube_video`; registry snapshot via the
  **batch** path → **45 CONTENT observations** (15 videos × 3 metrics).
- **Hourly idempotency**: channel snapshot created 3, same-hour rerun created **0**.
- **Quota buckets**: `quota_date=2026-08-08` while UTC was 2026-08-09 → **provider-tz reset proven**;
  SEARCH 300/3500, GENERAL_READ 6/4500, VIDEO_STATS_BATCH 1/1500; usable 9500, reserve 500.
- **Artist-universe** diag: 18 candidates (16 YouTube_search + 2 event-derived), 2 CONFIRMED_LIVE_INDIA,
  15 videos, `by_source EVENT 2 / YOUTUBE_SEARCH 16`. **Gateway BFF** `/admin/v1/demand/overview` surfaces
  artist-universe + quota-buckets (all downstream true); the **Demand Intelligence** admin screen renders
  both read-only cards. Trends stays `ACCESS_UNAVAILABLE` (import-only) — correct. No fabricated data.

## Phase 5A.2 — demand inspection surface (2026-08-08, LIVE VALIDATED, docker + browser)

api-gateway + artist-intelligence-service rebuilt/recreated (`ADMIN_API_ENABLED=ADMIN_LOCAL_MODE=true`,
`DEMAND_INTELLIGENCE_ENABLED=YOUTUBE_ENABLED=DEMAND_SCHEDULER_ENABLED=true`), over the real accrued
Phase 5A demand data. All through the browser (`localhost:5174`, vite `/api`→gateway proxy) + curl:
- **Demand Intelligence** screen renders: coverage (3 with YT identity / 1 RESOLVED / 1 AMBIGUOUS / 5
  regions), **YouTube = REAL** (mode from signal `/health`), quota today (8 req / 2 search / 200 units),
  scheduler **enabled** (2 SUCCEEDED, latest refresh stamped), Trends **ACCESS_UNAVAILABLE** (neutral note).
- **Artist Demand** for `artist:arijit-singh`: RESOLVED YouTube `UCtFOW7jJXChfFNoucRFqRmw` **provider-verified
  2026-08-08** (the scheduler re-verified via channels.list on its pass) + a REJECTED candidate
  (`insufficient_margin`) + RESOLVED Trends SEARCH_TERM; real snapshot **6,350,000 subs** (rounding caveat) /
  4.877B views / 174 videos, fresh; momentum honestly **INSUFFICIENT_HISTORY**; Trends 5 regions
  (IN-WB 100 … IN-DL 69) with normalization context; geography sortable; **0 observed supply** (Arijit not
  in the indie inventory — honest). Also renders embedded on the ARTIST entity page.
- **Event → Demand context** (`event:live-music-featuring-ishani-nag`): 2 resolved artists, both
  `INSUFFICIENT_HISTORY` (honest — no accrued demand history), "co-movement only" labelled.
- **Bounded observations**: total 41, `limit=999` → 422 (server clamp).
- **Degraded isolation**: demand-service stopped → `/demand/overview` returns **200 `available:false`**
  while `/dashboard` + `/events` stay **200**; recovers on restart. **No mutation controls** anywhere
  (GET-only BFF; POST to a demand path → 405).
- No fabricated observations were created to populate screens; all data is real prior-phase accrual.

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
crawl **202** (+2 in 5A.3.2: create-artist registry write + graph-only reconcile idempotency) ·
gateway **116** (+14 Admin D OIDC/boundary; +18 Admin D.1: cryptographic id_token verification (forged/
unknown-key/kid/alg=none/iss/aud/expiry/email/domain/hd-spoof/nonce), hard read-only enforcement,
session-cookie flags, expired-state; +4 Admin D.2: `/v1/platform/status` authenticated —
unauth 401 / viewer 200 / admin-disabled 404 / local-mode open; +9 5B.1: research-watchlist BFF —
auth required / created_by forwarded / research-config flag gates writes / canonical mutations still
blocked; +1 5B.1.1: canonical-integrity read; +3 5B.2i1: artist coverage/movement + market movement BFF;
+6 5B.2i2: catalog Artists/Venues — canonical-only / no graph inflation / monitoring merge / filter /
degraded / read-only) · signal **107** (+4 in 5B.1: resolve @handle / video id → channel id, FOUND/NOT_FOUND; +2 in 5A.3.3:
`/health/ready` observation-dependency guard ok/503) · graph **60** · analytics **45** · media **43** ·
observation **11** · entity **12** ·
**artist-intelligence 107** (5A.3: candidate universe/quota/hourly/cadence; 5A.3.1: promotion/ecosystem/
allocation/event-proximity; +4 in 5A.3.2: canonical-reconciliation diagnostics + orphan audit + safe
degrade; +3 in 5A.3.3: crawl `/entities` TTL-cache collapse / per-page / disable; +15 in 5B.1: watch-target
intake — name-only pending / existing-canonical link / channel-URL verified identity / duplicate + bulk
idempotency / no-fabricated-canonical / hint-requires-verification / pause suspends + resume restores
scheduling / diagnostics / re-resolve; +5 in 5B.1.1: canonical-reference invariant — unregistered operator
selection stays pending / stale candidate canonical not trusted / WATCHING requires registry-backed id /
canonical-integrity exposes orphans / provider-only never fabricates canonical; +12 in 5B.2: content-movement
engine — velocity/acceleration, INSUFFICIENT_HISTORY, RISING/BREAKOUT_CANDIDATE/COOLING with evidence, age-
normalised baseline, no fused score, owned/ecosystem distinct, NOT_FOUND preserves history; +1 Data Coverage
distinguishes collected/unavailable/insufficient/zero; +2 5B.2i2: monitoring summaries batch + coverage) — all pass. artist-intelligence Alembic **001↔002↔003
upgrade+downgrade+re-upgrade verified** (additive/reversible). Frontend `tsc -b` + `vite build` clean; `npm run lint` exit 0
(Admin D redesign: only the pre-existing `only-export-components` warnings on the shared primitives module). No JS test runner in the repo (Admin A–D convention): the UI is
type-checked + browser-validated, with automated coverage on the BFF/read models it consumes. Lint clean
except baseline-tolerated B008 (FastAPI `Depends`) and one pre-existing S110 in an alembic migration.

## Invariants / constraints (must hold)
- Deterministic + explainable only; **no LLM** in detection/matching/enrichment.
- Never report a failed request as record absence (absence is authoritative 404 only).
- Never fabricate values or a live match; fixtures for mechanics must be labelled.
- Additive/reversible migrations; feature flags default **off**; existing behavior preserved.
- `docs/product-spec.md` (MCP) is append-only — never overwrite.
- No PII/fingerprinting; don't persist full third-party HTML indefinitely; images hotlinked not re-hosted.
- BookMyShow stays partner_feed (never evasively scraped); no CAPTCHA/bot-evasion.
- **Demand (YouTube/Trends) and supply (Shadow Ledger) are separate evidence systems** meeting only at
  `canonical_artist_id`; demand metrics never enter the event Shadow Ledger; the demand layer never
  creates a canonical artist. No composite popularity/value/booking score; no causal claim from
  co-movement. Google Trends is relative interest (never absolute volume); no unofficial scraping.
- **(5A.3)** artist discovery produces **candidates**, never canonical artists (the demand layer never
  creates a canonical ARTIST); India market presence is **evidence-based**, never a single relevance
  score; identity thresholds are never lowered to raise coverage (false matches are worse than missing
  data); YouTube search is spent on discovery/resolution, never on known-id refresh; quota targets high
  utilisation with a reserve and **defers** (never invalidates identities) at the reserve; hourly
  precision is never retrofitted onto historical daily observations; BookMyShow is never a gatekeeper and
  is never evasively scraped.
- API keys: user adds to `.env`; never handled/pasted by the agent.
- **Admin surface access (Admin D)**: the **local** console runs unauthenticated only under
  `ADMIN_LOCAL_MODE`, which is **never** set on any cloud manifest (would expose it with no login — enforced
  by test). The **one public console** (`nquark-admin`) is exposed only behind **Google Workspace OIDC**
  (verified-email domain allowlist, deny-by-default, single read-only role, httpOnly signed session cookie)
  and stays **operationally read-only**; its manifest must have OIDC on + local-mode off + actions off
  (enforced by test). Secrets are never inlined. The private services + the crawl-space `nquark-api-gateway`
  never serve the console. See `docs/deployment.md`.

## Recommended next phase
**Prod collection is now unblocked and accruing** (5A.3.3: observation-service deployed; observations,
graph nodes, and canonical artists growing). **Immediate follow-ups**: (1) **District paged/date-ordered
discovery** — District's `discover(limit=50)` reads the head of one unordered 24k-URL sitemap (historical
2022–2025 events), so it enrolls mostly past events that capture `RECORD_ABSENT`; needs city/date-ordered
paging (same class as the deferred Kolkata-first Skillbox crawl). (2) **Reap the ~24.5k `FAILED_RETRYABLE`
capture jobs** from the pre-fix 502 era — they now retry against a working ingest and will settle
(present/absent), but a bounded cleanup avoids churn. (3) Optionally deploy **entity-service** (currently
best-effort/undeployed; signal's entity resolution degrades gracefully and crawl owns canonical identity —
so it improves resolution quality but isn't required). Then let the collector run and observe accrued
Shadow Ledger + demand history.
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
