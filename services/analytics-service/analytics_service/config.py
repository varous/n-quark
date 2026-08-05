import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def detect_network_mode() -> str:
    explicit = os.environ.get("NQUARK_NETWORK_MODE", "").lower()
    if explicit in ("local", "docker"):
        return explicit
    if Path("/.dockerenv").exists():
        return "docker"
    return "local"


def default_graph_service_url() -> str:
    if detect_network_mode() == "docker":
        return "http://graph-service:8006"
    return "http://localhost:8006"


def default_crawl_service_url() -> str:
    if detect_network_mode() == "docker":
        return "http://crawl-service:8001"
    return "http://localhost:8001"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NQUARK_", env_file=".env", extra="ignore")

    service_name: str = "analytics-service"
    port: int = 8007
    log_level: str = "info"
    network_mode: str = Field(default_factory=detect_network_mode)
    graph_service_url: str = Field(default_factory=default_graph_service_url)
    crawl_service_url: str = Field(default_factory=default_crawl_service_url)
    analytics_max_events: int = 500  # bound on tracked events per aggregation (crawl endpoint max)
    postgres_url: str = "postgresql+psycopg://nquark:nquark@postgres:5432/nquark"
    redis_url: str = "redis://redis:6379/0"
    neo4j_url: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "nquarkdev"
    qdrant_url: str = "http://qdrant:6333"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "nquark"
    minio_secret_key: str = "nquark"


settings = Settings()
