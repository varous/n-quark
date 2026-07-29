#!/usr/bin/env bash
set -euo pipefail

echo "Running database migrations..."
alembic upgrade head

echo "Starting observation-service..."
exec uvicorn observation_service.main:app --host 0.0.0.0 --port "${NQUARK_PORT:-8004}"
