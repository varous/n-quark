#!/usr/bin/env bash
set -euo pipefail

# Run gateway governance migrations only when the admin API is enabled — keeps the plain proxy
# deployment unchanged and does not require Postgres for a health-only gateway.
if [ "${NQUARK_ADMIN_API_ENABLED:-false}" = "true" ]; then
  echo "admin api enabled — running gateway database migrations..."
  alembic upgrade head
fi

echo "Starting api-gateway..."
exec uvicorn api_gateway.main:app --host 0.0.0.0 --port "${NQUARK_PORT:-8000}"
