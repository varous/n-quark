.PHONY: help up down build logs test lint frontend api-gateway dev-local observation-migrate

help:
	@echo "n-quark development commands"
	@echo ""
	@echo "  make up          Start all services (Docker Compose)"
	@echo "  make down        Stop all services"
	@echo "  make build       Build all Docker images"
	@echo "  make logs        Tail Docker Compose logs"
	@echo "  make test        Run Python service tests"
	@echo "  make lint        Run ruff on all services"
	@echo "  make frontend    Start frontend dev server locally"
	@echo "  make api-gateway Start api-gateway locally"
	@echo "  make dev-local   Start all backend services on localhost (no Docker)"
	@echo "  make observation-migrate  Run observation-service Alembic migrations"

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

logs:
	docker compose logs -f

test:
	@for svc in services/*/; do \
		echo "Testing $$svc"; \
		cd "$$svc" && pip install -e ".[dev]" -q && pytest -q && cd ../..; \
	done

lint:
	@for svc in services/*/; do \
		echo "Linting $$svc"; \
		cd "$$svc" && pip install -e ".[dev]" -q && ruff check . --exclude .venv && cd ../..; \
	done

frontend:
	cd frontend && npm run dev

api-gateway:
	cd services/api-gateway && NQUARK_NETWORK_MODE=local uvicorn api_gateway.main:app --reload --port 8000

dev-local:
	bash scripts/dev-local.sh

observation-migrate:
	cd services/observation-service && alembic upgrade head
