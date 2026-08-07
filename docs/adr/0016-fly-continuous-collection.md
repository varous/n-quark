# ADR 0016 — Continuous private collection on Fly.io (Phase 4D)

- Status: Accepted
- Date: 2026-08-07
- Phase: 4D — Continuous Collection Deployment on Fly.io
- Relates to: [deploy/fly/README.md](../../deploy/fly/README.md), [docs/deployment.md](../deployment.md),
  ADR-0006 (scheduled capture)

## Context

The collection spine (crawl/signal/graph/media) needs to run **continuously and privately** on Fly.io —
both *enrolling* new Boshow/District events and *capturing* tracked ones — without deploying any public
surface (admin frontend, api-gateway, analytics). Discovery was manual (`/sync`) and capture was a
run-to-completion worker (cron pattern). Two questions needed durable answers: how does recurring
collection run, and how does the app talk to Fly Managed Postgres.

## Decisions

1. **In-process collector, not a second scheduler.** A bounded background loop inside crawl-service
   (`collector.py`, flag-gated `COLLECTOR_ENABLED`) periodically runs the **existing** scheduler
   (`sync_from_refs` for discovery/enrollment + `run_once` for capture). It is not a new scheduling
   architecture and not a separate cron machine — it keeps the four-app topology intact and honours the
   "crawl scheduler always running" requirement (`auto_stop_machines="off"`, `min_machines_running=1`).
   The run-to-completion worker and a one-shot `bootstrap` command remain available and share one
   full-pipeline scheduler builder (`deps.build_scheduler`).

2. **Restart-safety via Postgres, not the loop.** All durable state (`tracked_event`,
   `scheduled_capture_job`, Shadow Ledger) lives in Postgres; the loop is stateless and idempotent
   (`sync_from_refs` upserts; jobs dedup on a capture window). A process restart therefore loses no
   tracked events, duplicates no work, and resets no Shadow Ledger state — verified locally (134 tracked
   / 138 jobs identical across a container restart; capture continued).

3. **Managed-Postgres pooled/direct split.** `DATABASE_URL` (pooled) is used for app traffic and
   `MIGRATION_DATABASE_URL` (direct) for Alembic/startup migrations, falling back to `DATABASE_URL` when
   unset. Both are normalized from Fly's `postgres://` to `postgresql+psycopg://`. `NQUARK_POSTGRES_URL`
   still overrides; local Docker/dev are unchanged. This is one persistence architecture, not two.

4. **Private Flycast topology, env-driven discovery.** Four private apps (region `sin`, no public IP,
   `force_https=false`) communicate over `<app>.flycast`. Service URLs are environment-driven
   (`NQUARK_*_SERVICE_URL`); actual Fly app names live only in `fly.toml`/deploy env, never in code.

5. **Cloud source + isolation posture.** Boshow + District only (Skillbox excluded from every source-set
   and from the collector, per ADR-0015/4C.1); Shadow Ledger + entity resolution + media observation +
   media fetch on; **media byte storage off** (no Fly Volume/object storage this phase). Best-effort
   isolation is preserved end to end: a media failure, an entity-resolution failure, or one source's
   failure never fails a capture or another source.

6. **No paid resources created by automation.** `fly-deploy.sh`/`fly-bootstrap.sh`/`fly-smoke.sh` only
   deploy/seed/verify; app creation, Postgres provisioning, volumes and IP allocation are explicit manual
   operator steps. The operator must verify `fly ips list` shows a private Flycast address only. The admin
   dashboard remains **local-only** and is never deployed.

## Consequences

- Continuous collection runs in one always-on crawl machine, reusing the proven scheduler; scaling to a
  dedicated cron machine later is still possible without changing the scheduler.
- The collector cadence is configurable; discovery is bounded per cycle. A very large catalogue would want
  cursor-based discovery, but Boshow/District are small.
- The private topology means smoke/bootstrap run from inside the Fly network (`fly ssh console`), not from
  an operator laptop — documented in the README.
