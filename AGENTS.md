## Cursor Cloud specific instructions

### Overview

n-quark is a microservices monorepo: 10 Python FastAPI services, a React frontend, and infrastructure via Docker Compose (PostgreSQL, Neo4j, Redis, Qdrant, MinIO).

### Running the stack

```bash
cp .env.example .env
docker compose up --build -d
```

Verify: `curl http://localhost:8000/v1/platform/status`

Frontend: http://localhost:5173 (proxies API via `/api` in dev, or set `VITE_API_URL`)

### Lint and test

```bash
make test    # pytest in each service under services/
make lint    # ruff check in each service
cd frontend && npm run build   # TypeScript + Vite build
```

### Single-service local dev (without Docker)

```bash
cd services/api-gateway
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Most services only expose `/health` and `/` in the scaffold; full stack health aggregation requires all services running (Docker Compose).

### Service ports

| Service | Port |
|---------|------|
| api-gateway | 8000 |
| crawl-service | 8001 |
| media-service | 8002 |
| signal-service | 8003 |
| observation-service | 8004 |
| entity-service | 8005 |
| graph-service | 8006 |
| analytics-service | 8007 |
| feature-service | 8008 |
| intelligence-service | 8009 |
| frontend | 5173 |

### Gotchas

- Docker Compose is required for end-to-end platform status; the api-gateway aggregates health from internal service hostnames (`crawl-service:8001`, etc.).
- Neo4j healthcheck may take ~30s on first boot.
- Default credentials are in `.env.example` (development only).
