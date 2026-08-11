#!/usr/bin/env bash
set -euo pipefail

# Run gateway governance migrations only when the admin API is enabled — keeps the plain proxy
# deployment unchanged and does not require Postgres for a health-only gateway.
#
# The migration is best-effort: the Admin D read-only console needs no gateway DB for its read models
# (only the audit log / governed writes do, and those are disabled in the console). So a missing or
# unreachable Postgres must not crash the container — it boots read-only and the audit store degrades.
# When a DB IS attached (local docker, or the console with `fly mpg attach`), migrations apply normally.
if [ "${NQUARK_ADMIN_API_ENABLED:-false}" = "true" ]; then
  echo "admin api enabled — running gateway database migrations..."
  if alembic upgrade head; then
    echo "migrations applied."
  else
    echo "WARNING: gateway migrations did not apply (no reachable Postgres?) — continuing read-only; audit disabled."
  fi
fi

echo "Starting api-gateway..."
exec uvicorn api_gateway.main:app --host 0.0.0.0 --port "${NQUARK_PORT:-8000}"
