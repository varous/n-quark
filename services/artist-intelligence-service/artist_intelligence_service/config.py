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


def _host(docker: str, local: str) -> str:
    return docker if detect_network_mode() == "docker" else local


def default_signal_service_url() -> str:
    return _host("http://signal-service:8003", "http://localhost:8003")


def default_graph_service_url() -> str:
    return _host("http://graph-service:8006", "http://localhost:8006")


def default_crawl_service_url() -> str:
    return _host("http://crawl-service:8001", "http://localhost:8001")


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

    service_name: str = "artist-intelligence-service"
    port: int = 8010
    log_level: str = "info"
    network_mode: str = Field(default_factory=detect_network_mode)
    postgres_url: str = Field(default_factory=default_postgres_url)

    # Acquisition is delegated to signal-service (the single ingestion path — no direct YouTube/Google
    # client here). Supply comes from graph + crawl (same pattern as analytics-service). Env-driven for
    # Flycast; actual Fly app names live only in deploy config, never in code.
    signal_service_url: str = Field(default_factory=default_signal_service_url)
    graph_service_url: str = Field(default_factory=default_graph_service_url)
    crawl_service_url: str = Field(default_factory=default_crawl_service_url)
    http_timeout_seconds: float = 20.0

    # --- Master switch: the demand layer is OFF by default (no migrations, no scheduler). ---
    demand_intelligence_enabled: bool = False

    # --- YouTube (via signal-service). The API key lives in signal-service secrets, not here. ---
    youtube_enabled: bool = False
    youtube_search_enabled: bool = True          # identity discovery only (bounded, 100 units/search)
    youtube_max_searches_per_day: int = 50       # quota discipline: refuse further searches past this
    youtube_channel_refresh_interval_seconds: int = 86400   # daily channel snapshot cadence
    youtube_video_refresh_interval_seconds: int = 43200     # recent-video snapshot cadence
    youtube_recent_video_limit: int = 5          # bounded window of recent videos per artist
    youtube_daily_quota_units: int = 10000       # informational cap for quota diagnostics

    # --- Google Trends: provider-neutral + feature-gated. Modes: OFFICIAL_API | IMPORT | DISABLED. ---
    # No unofficial scraping. OFFICIAL_API only if valid alpha credentials/config are present, else it
    # reports ACCESS_UNAVAILABLE and IMPORT (legitimate CSV exports) is the interim fallback.
    google_trends_mode: str = "IMPORT"
    google_trends_api_key: str = ""              # alpha credential; empty => official mode unavailable
    google_trends_api_base: str = ""             # alpha endpoint; empty => official docs inaccessible
    google_trends_default_region: str = "IN"

    # --- Refresh scheduler (in-process, restart-safe; OFF by default). ---
    demand_scheduler_enabled: bool = False
    demand_refresh_interval_seconds: int = 300   # how often the loop drains due jobs
    demand_scheduler_batch_size: int = 20        # max jobs per drain (bounded concurrency)
    demand_scheduler_lock_ttl_seconds: int = 300
    demand_scheduler_max_attempts: int = 5
    demand_scheduler_backoff_base_seconds: int = 300
    demand_scheduler_backoff_max_seconds: int = 21600

    # --- Deterministic read-model thresholds (transparent + configurable). ---
    demand_min_observations_for_delta: int = 2   # below this: INSUFFICIENT_HISTORY
    demand_freshness_stale_hours: int = 48       # observation older than this is "stale"
    geo_affinity_high_interest_threshold: int = 60   # relative to analysed cohort (0-100 scale)
    geo_affinity_low_interest_threshold: int = 25
    demand_cohort_max_artists: int = 30          # bounded pilot cohort size

    @property
    def resolved_trends_mode(self) -> str:
        """OFFICIAL_API only if the operator asked for it AND alpha credentials+endpoint exist;
        otherwise fall back honestly to IMPORT (the official provider reports ACCESS_UNAVAILABLE)."""
        mode = (self.google_trends_mode or "IMPORT").upper()
        if mode == "OFFICIAL_API" and self.google_trends_api_key and self.google_trends_api_base:
            return "OFFICIAL_API"
        if mode == "OFFICIAL_API":
            return "IMPORT"
        if mode in ("IMPORT", "DISABLED"):
            return mode
        return "IMPORT"

    @property
    def migration_database_url(self) -> str:
        """DB URL for Alembic/startup migrations: MIGRATION_DATABASE_URL if set, else the app URL."""
        return normalize_db_url(os.environ.get("MIGRATION_DATABASE_URL")) or self.postgres_url


settings = Settings()
