#!/usr/bin/env bash
# Phase 4D — deploy the four PRIVATE collection services to Fly.io in dependency order.
# Deploys graph -> signal -> media -> crawl. NEVER deploys admin/gateway/analytics.
# Does NOT create paid resources (no `apps create`, no `pg create`, no IP allocation) — the operator
# must have already created the apps + attached Fly Managed Postgres and set DB secrets. Fails on an
# unhealthy dependency before continuing. Set DRY_RUN=1 to print the commands without executing.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${FLY_REGION:-sin}"
GRAPH_APP="${GRAPH_APP:-nquark-graph-service}"
SIGNAL_APP="${SIGNAL_APP:-nquark-signal-service}"
MEDIA_APP="${MEDIA_APP:-nquark-media-service}"
CRAWL_APP="${CRAWL_APP:-nquark-crawl-service}"

run() { echo "+ $*"; [ "${DRY_RUN:-0}" = "1" ] || "$@"; }

command -v flyctl >/dev/null 2>&1 || { echo "flyctl not found — install it and 'fly auth login' first." >&2; exit 1; }

# deploy <app> <service-dir> <toml> <port>
deploy_one() {
  local app="$1" dir="$2" toml="$3" port="$4"
  echo "== deploying ${app} (region ${REGION}) =="
  run flyctl deploy --now --app "${app}" --config "${REPO}/deploy/fly/${toml}" \
      --dockerfile "${REPO}/services/${dir}/Dockerfile" "${REPO}/services/${dir}"
  echo "-- health check ${app} --"
  # private service: run the health check from inside the machine (flycast has no public route)
  run flyctl ssh console --app "${app}" -C "curl -sf http://localhost:${port}/health" \
    || { echo "DEPENDENCY UNHEALTHY: ${app} — aborting." >&2; exit 1; }
}

deploy_one "${GRAPH_APP}"  graph-service  graph-service.toml  8006
deploy_one "${SIGNAL_APP}" signal-service signal-service.toml 8003
deploy_one "${MEDIA_APP}"  media-service  media-service.toml  8002
deploy_one "${CRAWL_APP}"  crawl-service  crawl-service.toml  8001

echo "All four collection services deployed. Verify private-only networking:  fly ips list -a ${CRAWL_APP}"
echo "(There must be NO public v4/v6 address — only a private Flycast address.)"
echo "Next: scripts/fly-bootstrap.sh (seed an empty DB), then scripts/fly-smoke.sh."
