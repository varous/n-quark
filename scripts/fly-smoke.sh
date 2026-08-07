#!/usr/bin/env bash
# Phase 4D — smoke-test the private collection deployment. Runs checks from INSIDE the crawl machine,
# which reaches signal/graph/media over Flycast (services are private; no public route). Does not
# require the local admin frontend. Read-only. Set DRY_RUN=1 to print the commands only.
set -euo pipefail

GRAPH_APP="${GRAPH_APP:-nquark-graph-service}"
SIGNAL_APP="${SIGNAL_APP:-nquark-signal-service}"
MEDIA_APP="${MEDIA_APP:-nquark-media-service}"
CRAWL_APP="${CRAWL_APP:-nquark-crawl-service}"

command -v flyctl >/dev/null 2>&1 || { echo "flyctl not found — install it and 'fly auth login' first." >&2; exit 1; }

# ssh <app> <shell-cmd>
ssh_run() { echo "== [$1] $2"; [ "${DRY_RUN:-0}" = "1" ] || flyctl ssh console --app "$1" -C "sh -lc '$2'"; }

echo "### 1. private connectivity + health (from crawl over Flycast) ###"
ssh_run "${CRAWL_APP}" "curl -sf http://localhost:8001/health"
ssh_run "${CRAWL_APP}" "curl -sf http://nquark-graph-service.flycast:8006/health"
ssh_run "${CRAWL_APP}" "curl -sf http://nquark-signal-service.flycast:8003/health"
ssh_run "${CRAWL_APP}" "curl -sf http://nquark-media-service.flycast:8002/health"

echo "### 2. migration heads + DB connectivity ###"
ssh_run "${GRAPH_APP}" "cd /app && alembic current"
ssh_run "${CRAWL_APP}" "cd /app && alembic current"
ssh_run "${MEDIA_APP}" "cd /app && alembic current"

echo "### 3. tracked events + scheduled jobs + collector state ###"
ssh_run "${CRAWL_APP}" "curl -sf http://localhost:8001/v1/internal/capture-schedule?limit=5"
ssh_run "${CRAWL_APP}" "curl -sf http://localhost:8001/v1/internal/capture-schedule/jobs?limit=5"
ssh_run "${CRAWL_APP}" "curl -sf http://localhost:8001/health | grep -o collector_enabled.*"

echo "### 4. capture success + Shadow Ledger persistence (pick a tracked event id from step 3) ###"
echo "    # curl http://nquark-graph-service.flycast:8006/v1/internal/events/<EVENT>/shadow-ledger"

echo "### 5. entity-resolution + media hook / isolation (in a capture trace) ###"
echo "    # a fresh capture trace shows entity_resolution + media outcomes; a media/entity failure"
echo "    # leaves result_code=SUCCESS_RECORD_PRESENT (capture never fails)."

echo "### 6. Skillbox disabled ###"
ssh_run "${SIGNAL_APP}" "curl -sf http://localhost:8003/v1/internal/sources | grep -o 'skillbox.\{0,30\}'"
echo "    # expect \"enabled\": false for skillbox; collector_sources must be [boshow, district] only."

echo "Smoke checks issued. Admin dashboard is LOCAL-ONLY and is not part of this deployment."
