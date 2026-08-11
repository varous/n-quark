import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_db_url(url: str | None) -> str | None:
    """Normalize a DB URL to the SQLAlchemy+psycopg driver (Fly Managed Postgres gives postgres://)."""
    if not url:
        return None
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def default_postgres_url() -> str:
    # Cloud (Fly Managed Postgres) provides DATABASE_URL for pooled app access; NQUARK_POSTGRES_URL
    # still overrides. Local Docker/dev keep their existing defaults.
    env = normalize_db_url(os.environ.get("DATABASE_URL"))
    if env:
        return env
    if Path("/.dockerenv").exists():
        return "postgresql+psycopg://nquark:nquark@postgres:5432/nquark"
    return "postgresql+psycopg://nquark:nquark@localhost:5432/nquark"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NQUARK_", env_file=".env", extra="ignore")

    service_name: str = "observation-service"
    port: int = 8004
    log_level: str = "info"
    postgres_url: str = Field(default_factory=default_postgres_url)
    redis_url: str = "redis://redis:6379/0"
    neo4j_url: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "nquark"
    qdrant_url: str = "http://qdrant:6333"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "nquark"
    minio_secret_key: str = "nquark"

    @property
    def migration_database_url(self) -> str:
        """DB URL for Alembic/startup migrations: MIGRATION_DATABASE_URL if set, else the app URL.

        DDL over a transaction pooler (PgBouncer) is unreliable, so the operator points this at the
        Managed Postgres *direct* endpoint while ``postgres_url`` (DATABASE_URL) uses the pooled one."""
        return normalize_db_url(os.environ.get("MIGRATION_DATABASE_URL")) or self.postgres_url


settings = Settings()
