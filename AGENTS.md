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

- **Errno -3 / name resolution failure**: The api-gateway resolves downstream services by hostname. Outside Docker Compose it defaults to `localhost:8001–8009`; inside Compose it uses Docker service names (`crawl-service`, etc.). Run `make dev-local` to start all backend services locally, or use `docker compose up` for the full stack. Override with `NQUARK_NETWORK_MODE=local|docker`.
- Docker Compose is required for end-to-end platform status when not using `make dev-local`.
- Neo4j healthcheck may take ~30s on first boot.
- Default credentials are in `.env.example` (development only).
