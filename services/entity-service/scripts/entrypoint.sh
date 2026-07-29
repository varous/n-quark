#!/usr/bin/env bash
set -euo pipefail

echo "Running database migrations..."
alembic upgrade head

echo "Starting entity-service..."
exec uvicorn entity_service.main:app --host 0.0.0.0 --port "${NQUARK_PORT:-8005}"
