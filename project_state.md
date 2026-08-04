# n-quark — Project State

_Last updated: 2026-08-04 (Phase 3.1). Branch `main`. Repo: github.com/varous/n-quark._

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

## Test status
crawl **190** · gateway **42** · signal **70** · graph **60** · analytics **11** · observation **11** ·
entity **12** — all pass. Frontend `tsc -b` + `vite build` clean. Gateway Alembic upgrade+downgrade
verified. Lint clean except baseline-tolerated B008 (FastAPI `Depends`).

## Invariants / constraints (must hold)
- Deterministic + explainable only; **no LLM** in detection/matching/enrichment.
- Never report a failed request as record absence (absence is authoritative 404 only).
- Never fabricate values or a live match; fixtures for mechanics must be labelled.
- Additive/reversible migrations; feature flags default **off**; existing behavior preserved.
- `docs/product-spec.md` (MCP) is append-only — never overwrite.
- No PII/fingerprinting; don't persist full third-party HTML indefinitely; images hotlinked not re-hosted.
- BookMyShow stays partner_feed (never evasively scraped); no CAPTCHA/bot-evasion.
- API keys: user adds to `.env`; never handled/pasted by the agent.

## Recommended next phase
**Admin Phase C — bulk-safe curation + IdP + deep reversal.** Guarded batch supersession/linking over a
reviewed candidate set (still explicit + audited, no blind bulk merge); Google Workspace OIDC via the
existing `authenticate` contract; true rollback of entity-resolution graph edges (not just mark-based);
and driving a canonical-vs-legacy **unification migration** from the accumulated `SUPERSEDED_BY` decisions.
Then resume the data track:

**3.2 — regional/market analytics over shared entities + unify the two entity id conventions.**
(1) Build minimal internal cross-inventory analytics on top of the Phase 3.1 canonical entities
(artist/venue/organizer footprints across sources, cities, series) — read-only, deterministic, no
prediction. (2) Reconcile the ingest-time naive entity projection (signal-service name-slug) with the
Phase 3.1 evidence-based canonical layer so the graph has one id convention. (3) Only then add a third,
city-overlapping source so cross-source entity convergence becomes non-zero in practice. Deferred:
matcher/threshold calibration against a real labelled overlap cohort, and accepted-match consensus
write-back — both still relevant once an overlapping source exists.
