#!/usr/bin/env bash
# Start all n-quark services on localhost for local development.
# Requires: pip install -e on each service (see Makefile test target).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${HOME}/.local/bin:${PATH}"
export NQUARK_NETWORK_MODE=local

SERVICES=(
  "crawl-service:8001:crawl_service.main:app"
  "media-service:8002:media_service.main:app"
  "signal-service:8003:signal_service.main:app"
  "observation-service:8004:observation_service.main:app"
  "entity-service:8005:entity_service.main:app"
  "graph-service:8006:graph_service.main:app"
  "analytics-service:8007:analytics_service.main:app"
  "feature-service:8008:feature_service.main:app"
  "intelligence-service:8009:intelligence_service.main:app"
  "api-gateway:8000:api_gateway.main:app"
)

PIDS=()

cleanup() {
  echo ""
  echo "Stopping services..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}

trap cleanup EXIT INT TERM

for entry in "${SERVICES[@]}"; do
  IFS=':' read -r name port module app <<< "$entry"
  svc_dir="${ROOT}/services/${name}"

  if [ ! -d "$svc_dir" ]; then
    echo "Missing service directory: $svc_dir" >&2
    exit 1
  fi

  echo "Starting ${name} on port ${port}..."
  (
    cd "$svc_dir"
    pip install -e . -q
    exec uvicorn "${module}:${app}" --host 127.0.0.1 --port "$port"
  ) &
  PIDS+=($!)
done

echo ""
echo "All services starting. API gateway: http://localhost:8000"
echo "Platform status: http://localhost:8000/v1/platform/status"
echo "Press Ctrl+C to stop."

wait
