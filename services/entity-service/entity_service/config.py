import re
import uuid
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_postgres_url() -> str:
    if Path("/.dockerenv").exists():
        return "postgresql+psycopg://nquark:nquark@postgres:5432/nquark"
    return "postgresql+psycopg://nquark:nquark@localhost:5432/nquark"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NQUARK_", env_file=".env", extra="ignore")

    service_name: str = "entity-service"
    port: int = 8005
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


settings = Settings()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower().strip())
    return slug.strip("-") or "unknown"


def artist_canonical_id(display_name: str, *, suffix: str | None = None) -> str:
    base = slugify(display_name)
    if suffix:
        return f"artist:{base}-{suffix}"
    return f"artist:{base}"
