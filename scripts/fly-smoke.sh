#!/usr/bin/env bash
set -euo pipefail

CRAWL_APP="${CRAWL_APP:-nquark-crawl-service}"
GRAPH_APP="${GRAPH_APP:-nquark-graph-service}"
SIGNAL_APP="${SIGNAL_APP:-nquark-signal-service}"
MEDIA_APP="${MEDIA_APP:-nquark-media-service}"

ssh_run() {
  local app="$1"
  shift
  local cmd="$*"

  echo "== [${app}] ${cmd}"
  flyctl ssh console --app "$app" -C "$cmd"
}

http_get() {
  local app="$1"
  local url="$2"

  ssh_run "$app" \
    "python -c 'import sys,urllib.request; r=urllib.request.urlopen(sys.argv[1], timeout=15); print(r.status); print(r.read().decode())' '$url'"
}

echo "### 1. private connectivity + health (from crawl over Flycast) ###"

http_get "$CRAWL_APP" "http://localhost:8001/health"
http_get "$CRAWL_APP" "http://${GRAPH_APP}.flycast/health"
http_get "$CRAWL_APP" "http://${SIGNAL_APP}.flycast/health"
http_get "$CRAWL_APP" "http://${MEDIA_APP}.flycast/health"

echo
echo "### 2. Fly native health checks ###"

flyctl checks list --app "$GRAPH_APP"
flyctl checks list --app "$SIGNAL_APP"
flyctl checks list --app "$MEDIA_APP"
flyctl checks list --app "$CRAWL_APP"

echo
echo "### 3. collector state ###"

http_get "$CRAWL_APP" "http://localhost:8001/health"

echo
echo "### 4. capture schedule ###"

http_get "$CRAWL_APP" \
  "http://localhost:8001/v1/internal/capture-schedule?limit=5"

http_get "$CRAWL_APP" \
  "http://localhost:8001/v1/internal/capture-schedule/jobs?limit=5"

echo
echo "### 5. signal source diagnostics ###"

http_get "$SIGNAL_APP" \
  "http://localhost:8003/v1/internal/sources"

echo
echo "Smoke test completed successfully."
