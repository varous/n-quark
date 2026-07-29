# n-quark

Intelligence Operating System for the live entertainment industry.

n-quark continuously observes the live entertainment ecosystem, converts observations into structured knowledge, and generates intelligence that enables better decisions.

## Documentation

- [Product Specification](docs/product-spec.md)
- [Architecture](docs/architecture.md)
- [Ontology](docs/ontology.md)

## Repository Structure

```
docs/                  Product, architecture, and ontology specs
services/              Python FastAPI microservices
  api-gateway/         Public API (port 8000)
  crawl-service/       Ingestion (8001)
  media-service/       Creative analysis (8002)
  signal-service/      External signal normalization (8003)
  observation-service/ Immutable observations (8004)
  entity-service/      Entity canonicalization (8005)
  graph-service/       Knowledge graph (8006)
  analytics-service/   Deterministic analytics (8007)
  feature-service/     ML feature store (8008)
  intelligence-service/ AI reasoning (8009)
frontend/              React + TypeScript + Tailwind dashboard
shared/                Shared Python schemas (nquark-common)
docker-compose.yml     Local development stack
```

## Quick Start

### Prerequisites

- Docker and Docker Compose
- Node.js 22+ (for local frontend development)
- Python 3.12+ (for local service development)

### Run the full stack

```bash
cp .env.example .env
docker compose up --build
```

Open **http://localhost:5173** for the intelligence dashboard (recent observations, entity lookup, demo ingest pipeline).

Services will be available at:

| Service | URL |
|---------|-----|
| API Gateway | http://localhost:8000 |
| Frontend | http://localhost:5173 |
| PostgreSQL | localhost:5432 |
| Neo4j Browser | http://localhost:7474 |
| Redis | localhost:6379 |
| Qdrant | http://localhost:6333 |
| MinIO Console | http://localhost:9001 |

Check platform health:

```bash
curl http://localhost:8000/v1/platform/status
```

### signal-service

Spotify adapter normalizes artist signals and writes append-only observations.

```bash
curl -X POST http://localhost:8003/v1/signals/spotify/artists/4tZwfgrHOc3mvqYFCOCYO6/ingest
```

Mock mode is enabled by default when Spotify credentials are not configured.

### entity-service

Canonical artist registry with alias resolution (`artist:spotify:*` → `artist:daft-punk`).

```bash
curl -X POST http://localhost:8005/v1/entities/artists/resolve-spotify/4tZwfgrHOc3mvqYFCOCYO6 \
  -H "Content-Type: application/json" -d '{"display_name": "Daft Punk"}'
```

### observation-service

Append-only observation store backed by PostgreSQL. See [AGENTS.md](AGENTS.md) for migrate/run instructions.

```bash
curl -X POST http://localhost:8004/v1/observations -H "Content-Type: application/json" \
  -d '{"entity":"artist:example","attribute":"genre","value":"electronic","source":"seed","confidence":0.9}'
```

### Local development (without Docker)

Start all backend services on localhost:

```bash
make dev-local
```

In another terminal, start the frontend:

```bash
cd frontend && npm run dev
```

The api-gateway auto-detects the environment: it uses `localhost:8001–8009` outside Docker and Docker service hostnames inside Compose. Override with `NQUARK_NETWORK_MODE=local` or `NQUARK_NETWORK_MODE=docker`.

### Local development (single service)

```bash
# Frontend only
cd frontend && npm install && npm run dev

# Single service (example: api-gateway)
cd services/api-gateway
pip install -e ".[dev]"
uvicorn api_gateway.main:app --reload --port 8000

# Run all service tests
make test
```

## Design Principles

- Observations are immutable (append-only)
- Deterministic computation before AI reasoning
- Every insight is explainable with provenance
- Services are independent with versioned schemas
- APIs expose intelligence, not database tables

## License

Proprietary — n-quark
