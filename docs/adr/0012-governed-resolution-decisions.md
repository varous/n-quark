# ADR 0012 — Governed resolution decisions & canonical supersession (Admin Phase B)

- Status: Accepted
- Date: 2026-08-04
- Phase: Admin Phase B — Governed Entity Resolution and Targeted Operations
- Extends: [ADR-0010](0010-cross-inventory-entity-resolution.md), [ADR-0011](0011-admin-console-bff.md)
- Relates to: [docs/admin-console.md](../admin-console.md), [docs/entity-resolution.md](../entity-resolution.md)

## Context

Admin Phase A gave a read-only console. Ambiguous entity evidence (`Pilu`, `BWS`), legacy-vs-canonical
duplication, and year-marker series false positives were *visible* but not *actionable*. Phase B turns
the console into a governed data-quality workbench: an authorized admin can resolve, create, link,
supersede and correct — every change explicit, role-authorized, validated, audited, versioned and
reversible. It is **not** a generic graph editor.

## Decisions

1. **Governance layer, owned pathways.** The admin BFF (gateway) owns *authorization + append-only
   decision records + audit + idempotency*; the actual entity/graph mutations run in crawl-service via
   the **reused Phase 3.1 pathways** (same canonical-id convention, source-handle registry, resolution
   history, graph relationship writes). No parallel entity-resolution or scheduling system.

2. **Gateway is now migration-managed.** New gateway Alembic (`alembic_version_gateway`, migration `001`)
   creates `admin_audit_log` + `admin_resolution_decision`; production no longer relies on runtime
   `create_all` (an isolated SQLite dev/test fallback remains, migration-compatible). Migrations run at
   boot when the admin API is enabled; the gateway migration version is exposed through system-health.
   Migration `001` adopts a pre-existing Phase A `admin_audit_log` idempotently.

3. **Append-only decisions, never destructive.** `admin_resolution_decision` is immutable except explicit
   reversal metadata. Manual decisions add higher-authority governance records; **original source
   evidence is never deleted or rewritten** — resolution history is appended, graph relationships are
   *superseded* (marked, not deleted).

4. **Role policy (server-side).** VIEWER read-only; ANALYST accept/reject/create/link/mark-unresolved/
   correct-series; OPERATOR + capture-now; **ADMIN only** for legacy supersession and decision reversal.
   An explicit reason is required for create / mark-alias / supersede / correct-series / reverse.

5. **Impact preview + idempotency + concurrency.** Every mutation offers a no-mutation preview (candidate,
   current/proposed target, affected handles/events, conflicting candidates, whether scheduler metadata or
   duplicate-event reconciliation is affected — both always false for entity resolution). Commands are
   idempotent on an idempotency key (repeat → `DECISION_ALREADY_APPLIED`), and surface explicit conflicts:
   `STALE_PREVIEW`, `HANDLE_ALREADY_LINKED`, `LEGACY_ALREADY_SUPERSEDED`,
   `REVERSAL_REQUIRES_MANUAL_DEPENDENCY_RESOLUTION`.

6. **Non-destructive supersession + honest counting.** A legacy node is superseded via a graph
   `legacy -SUPERSEDED_BY-> canonical` edge + an `entity_supersession` row; the legacy node and its
   historical edges are preserved, admin reads resolve the alias to the canonical, and canonical counts
   **deduplicate** superseded legacy ids (raw / canonical-resolved / legacy-superseded exposed separately).
   Only ADMIN performs it; it is reversible (`unsupersede`).

7. **Year-only series safeguard.** Series evidence now requires a *strong* marker
   (edition/volume/season/roman, incl. ordinal "5th Edition" and "Vol. 3") — a bare year no longer creates
   a series ("F1 2026" / "India Tour 2026" / "Summer 2026" produce none). A manual `CORRECT_EVENT_SERIES`
   decision can unlink an incorrect series, link an existing one, or create a valid one (preserving the
   prior relationship in history).

8. **Safe targeted capture-now.** A scheduler-owned `capture_now` creates/reuses one job through the
   **normal** claim → capture → Shadow Ledger → enrichment/resolution path (never calling adapters from the
   gateway), idempotent within a one-minute window; a failed request never becomes absence. OPERATOR/ADMIN
   only, audited.

## Live evidence (2026-08-04, docker + browser, real Boshow+District data)

Gateway migration `001` applied at boot (version shown in system-health). ANALYST created a canonical
`artist:pilu` from the ambiguous `Pilu` candidate (idempotent re-submit → `already_applied`;
`expected_status` mismatch → 409 `STALE_PREVIEW`; VIEWER → 403). ADMIN superseded the legacy
`venue:the-urban-theatre-project` onto `venue:urban-theatre-project--kolkata` (legacy node + edges
preserved, `SUPERSEDED_BY` edge added, canonical count 47→46, superseded 1; ANALYST → 403). ANALYST
unlinked the weak `F1 2026` year-series via `CORRECT_EVENT_SERIES`. ADMIN reversed the `Pilu` create —
the candidate returned to `AMBIGUOUS` and the Resolution Workbench showed the full history
`— → AMBIGUOUS → RESOLVED (MANUAL_CREATE) → AMBIGUOUS (REVERSED)`. OPERATOR `capture-now` on a real
Boshow event ran the normal job path (authoritative absence, idempotent on repeat). Every command was
audited and recorded as an append-only decision.

## Consequences

- The console is a governed workbench; identity quality can be curated safely, with a full audit trail
  and reversal.
- The naive-vs-canonical duplication can now be retired one supersession at a time, non-destructively,
  ahead of any bulk migration.
- Deep reversal of superseded graph writes is *mark-based* (edges are never deleted); a true rollback of
  entity-resolution graph edges is a documented future refinement.
