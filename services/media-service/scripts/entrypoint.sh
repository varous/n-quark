#!/usr/bin/env bash
set -euo pipefail

# Only migrate when observation is enabled — keeps the scaffold's default behaviour unchanged and
# does not require Postgres for a plain health-only deployment.
if [ "${NQUARK_MEDIA_OBSERVATION_ENABLED:-false}" = "true" ]; then
  echo "media_observation enabled — running database migrations..."
  alembic upgrade head
fi

echo "Starting media-service..."
exec uvicorn media_service.main:app --host 0.0.0.0 --port "${NQUARK_PORT:-8002}"
