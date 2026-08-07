#!/usr/bin/env bash
set -euo pipefail

# Only migrate when the demand layer is enabled — keeps a plain health-only deployment from needing
# Postgres, mirroring media-service's conditional-migration pattern.
if [ "${NQUARK_DEMAND_INTELLIGENCE_ENABLED:-false}" = "true" ]; then
  echo "demand_intelligence enabled — running database migrations..."
  alembic upgrade head
fi

echo "Starting artist-intelligence-service..."
exec uvicorn artist_intelligence_service.main:app --host 0.0.0.0 --port "${NQUARK_PORT:-8010}"
