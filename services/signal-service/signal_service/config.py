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


def default_graph_service_url() -> str:
    if detect_network_mode() == "docker":
        return "http://graph-service:8006"
    return "http://localhost:8006"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NQUARK_", env_file=".env", extra="ignore")

    service_name: str = "signal-service"
    port: int = 8003
    log_level: str = "info"
    network_mode: str = Field(default_factory=detect_network_mode)
    observation_service_url: str = Field(default_factory=default_observation_service_url)
    entity_service_url: str = Field(default_factory=default_entity_service_url)
    graph_service_url: str = Field(default_factory=default_graph_service_url)
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_api_base: str = "https://api.spotify.com/v1"
    spotify_token_url: str = "https://accounts.spotify.com/api/token"
    spotify_mock_mode: bool = False
    youtube_api_key: str = ""
    youtube_api_base: str = "https://www.googleapis.com/youtube/v3"
    youtube_region_code: str = "IN"
    youtube_mock_mode: bool = False
    musicbrainz_api_base: str = "https://musicbrainz.org/ws/2"
    musicbrainz_user_agent: str = "n-quark/0.1 (https://github.com/varous/n-quark)"
    musicbrainz_mock_mode: bool = False
    # Google Trends has no reliable free API, so the provider is pluggable.
    # "dataforseo" is the production reference; "serpapi" is a dev option; else "mock".
    google_trends_provider: str = "mock"
    google_trends_region: str = "IN"
    dataforseo_login: str = ""
    dataforseo_password: str = ""
    dataforseo_api_base: str = "https://api.dataforseo.com/v3"
    serpapi_key: str = ""
    serpapi_api_base: str = "https://serpapi.com/search.json"
    # Ticketing has no single canonical API, so the provider is pluggable.
    # "mock" (default, seeded with real Boshow data), "boshow" (public_scrape), or
    # "bookmyshow" (partner-feed scaffold — raises until a data partnership exists).
    ticketing_provider: str = "mock"
    boshow_api_base: str = "https://www.boshow.in/api"
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

    @property
    def use_musicbrainz_mock(self) -> bool:
        # MusicBrainz needs no API key — it is open — so mock only when explicitly set.
        return self.musicbrainz_mock_mode

    @property
    def resolved_trends_provider(self) -> str:
        """The Trends provider to actually use — falls back to mock if creds are missing."""
        provider = self.google_trends_provider.lower()
        if provider == "dataforseo" and self.dataforseo_login and self.dataforseo_password:
            return "dataforseo"
        if provider == "serpapi" and self.serpapi_key:
            return "serpapi"
        return "mock"


settings = Settings()
