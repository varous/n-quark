#!/usr/bin/env bash
set -euo pipefail

# Only migrate when the scheduler is enabled — keeps the scaffold's default behaviour unchanged and
# does not require Postgres for a plain health-only deployment.
if [ "${NQUARK_SCHEDULED_CAPTURE_ENABLED:-false}" = "true" ]; then
  echo "scheduled_capture enabled — running database migrations..."
  alembic upgrade head
fi

echo "Starting crawl-service..."
exec uvicorn crawl_service.main:app --host 0.0.0.0 --port "${NQUARK_PORT:-8001}"
