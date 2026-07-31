#!/usr/bin/env bash
set -euo pipefail

# Only the Postgres backend has a relational schema to migrate. The neo4j/memory backends
# have no Postgres tables, and their deployments may not even have Postgres reachable.
if [ "${NQUARK_GRAPH_BACKEND:-postgres}" = "postgres" ]; then
  echo "graph_backend=postgres — running database migrations..."
  alembic upgrade head
fi

echo "Starting graph-service..."
exec uvicorn graph_service.main:app --host 0.0.0.0 --port "${NQUARK_PORT:-8006}"
