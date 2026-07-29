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


def default_observation_service_url() -> str:
    if detect_network_mode() == "docker":
        return "http://observation-service:8004"
    return "http://localhost:8004"


def default_entity_service_url() -> str:
    if detect_network_mode() == "docker":
        return "http://entity-service:8005"
    return "http://localhost:8005"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NQUARK_", env_file=".env", extra="ignore")

    service_name: str = "signal-service"
    port: int = 8003
    log_level: str = "info"
    network_mode: str = Field(default_factory=detect_network_mode)
    observation_service_url: str = Field(default_factory=default_observation_service_url)
    entity_service_url: str = Field(default_factory=default_entity_service_url)
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_api_base: str = "https://api.spotify.com/v1"
    spotify_token_url: str = "https://accounts.spotify.com/api/token"
    spotify_mock_mode: bool = False
    youtube_api_key: str = ""
    youtube_api_base: str = "https://www.googleapis.com/youtube/v3"
    youtube_region_code: str = "IN"
    youtube_mock_mode: bool = False
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
    def use_spotify_mock(self) -> bool:
        if self.spotify_mock_mode:
            return True
        return not (self.spotify_client_id and self.spotify_client_secret)

    @property
    def use_youtube_mock(self) -> bool:
        if self.youtube_mock_mode:
            return True
        return not self.youtube_api_key


settings = Settings()
