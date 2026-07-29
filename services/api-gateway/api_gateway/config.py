import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

NetworkMode = Literal["local", "docker"]

DOCKER_DOWNSTREAM_SERVICES: dict[str, str] = {
    "crawl": "http://crawl-service:8001",
    "media": "http://media-service:8002",
    "signal": "http://signal-service:8003",
    "observation": "http://observation-service:8004",
    "entity": "http://entity-service:8005",
    "graph": "http://graph-service:8006",
    "analytics": "http://analytics-service:8007",
    "feature": "http://feature-service:8008",
    "intelligence": "http://intelligence-service:8009",
}

LOCAL_DOWNSTREAM_SERVICES: dict[str, str] = {
    "crawl": "http://localhost:8001",
    "media": "http://localhost:8002",
    "signal": "http://localhost:8003",
    "observation": "http://localhost:8004",
    "entity": "http://localhost:8005",
    "graph": "http://localhost:8006",
    "analytics": "http://localhost:8007",
    "feature": "http://localhost:8008",
    "intelligence": "http://localhost:8009",
}


def detect_network_mode() -> NetworkMode:
    explicit = os.environ.get("NQUARK_NETWORK_MODE", "").lower()
    if explicit in ("local", "docker"):
        return explicit  # type: ignore[return-value]
    if Path("/.dockerenv").exists():
        return "docker"
    return "local"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NQUARK_", env_file=".env", extra="ignore")

    service_name: str = "api-gateway"
    port: int = 8000
    log_level: str = "info"
    network_mode: NetworkMode = Field(default_factory=detect_network_mode)
    postgres_url: str = "postgresql+psycopg://nquark:nquark@postgres:5432/nquark"
    redis_url: str = "redis://redis:6379/0"
    neo4j_url: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "nquark"
    qdrant_url: str = "http://qdrant:6333"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "nquark"
    minio_secret_key: str = "nquark"

    @property
    def downstream_services(self) -> dict[str, str]:
        if self.network_mode == "docker":
            return DOCKER_DOWNSTREAM_SERVICES
        return LOCAL_DOWNSTREAM_SERVICES


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
