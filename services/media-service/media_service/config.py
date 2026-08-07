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


def _default(url_docker: str, url_local: str) -> str:
    return url_docker if detect_network_mode() == "docker" else url_local


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
    return normalize_db_url(os.environ.get("DATABASE_URL")) or \
        "postgresql+psycopg://nquark:nquark@postgres:5432/nquark"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NQUARK_", env_file=".env", extra="ignore")

    service_name: str = "media-service"
    port: int = 8002
    log_level: str = "info"
    network_mode: str = Field(default_factory=detect_network_mode)
    postgres_url: str = Field(default_factory=default_postgres_url)
    graph_service_url: str = Field(default_factory=lambda: _default(
        "http://graph-service:8006", "http://localhost:8006"))

    @property
    def migration_database_url(self) -> str:
        """DB URL for Alembic/startup migrations: MIGRATION_DATABASE_URL if set, else the app URL."""
        return normalize_db_url(os.environ.get("MIGRATION_DATABASE_URL")) or self.postgres_url

    # --- Phase 4B: creative-asset observation (all OFF by default) ---
    media_observation_enabled: bool = False    # master gate for the observation surface
    media_fetch_enabled: bool = False          # actually download bytes (else URL-only observations)
    media_storage_enabled: bool = False        # persist bytes to the content-addressed store
    media_graph_link_enabled: bool = False      # write USES_CREATIVE edges to graph-service
    media_max_bytes: int = 5_000_000           # hard cap on a fetched asset
    media_fetch_timeout_seconds: float = 10.0
    media_redirect_limit: int = 3
    media_allowed_mime_types: str = "image/jpeg,image/png,image/webp,image/gif"
    media_sources: str = "boshow,district"
    media_storage_dir: str = "/data/media"     # local content-addressed filesystem store
    # SSRF guard: allow loopback/private targets only when explicitly opted in (tests/dev)
    media_allow_private_networks: bool = False

    @property
    def allowed_mime_set(self) -> set[str]:
        return {m.strip().lower() for m in self.media_allowed_mime_types.split(",") if m.strip()}

    @property
    def media_source_set(self) -> set[str]:
        return {s.strip() for s in self.media_sources.split(",") if s.strip()}


settings = Settings()
