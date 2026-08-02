from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_postgres_url() -> str:
    if Path("/.dockerenv").exists():
        return "postgresql+psycopg://nquark:nquark@postgres:5432/nquark"
    return "postgresql+psycopg://nquark:nquark@localhost:5432/nquark"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NQUARK_", env_file=".env", extra="ignore")

    service_name: str = "graph-service"
    port: int = 8006
    log_level: str = "info"
    graph_backend: str = "postgres"  # "postgres" (consolidated) | "neo4j" | "memory" (tests)
    postgres_url: str = Field(default_factory=default_postgres_url)
    # Shadow Ledger (Phase 1). Disabling it makes the internal /v1/internal/... write path a no-op;
    # the public /v1/graph and /v1/events contracts are unaffected either way.
    shadow_ledger_enabled: bool = True
    # Consecutive *authoritative* absences before EVENT_DISAPPEARED (never on a single failed crawl).
    shadow_ledger_disappearance_threshold: int = 2
    redis_url: str = "redis://redis:6379/0"
    neo4j_url: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "nquarkdev"  # Neo4j 5 requires >= 8 chars
    qdrant_url: str = "http://qdrant:6333"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "nquark"
    minio_secret_key: str = "nquark"


settings = Settings()
