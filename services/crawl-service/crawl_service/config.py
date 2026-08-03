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


def default_postgres_url() -> str:
    if Path("/.dockerenv").exists():
        return "postgresql+psycopg://nquark:nquark@postgres:5432/nquark"
    return "postgresql+psycopg://nquark:nquark@localhost:5432/nquark"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NQUARK_", env_file=".env", extra="ignore")

    service_name: str = "crawl-service"
    port: int = 8001
    log_level: str = "info"
    network_mode: str = Field(default_factory=detect_network_mode)
    postgres_url: str = Field(default_factory=default_postgres_url)
    redis_url: str = "redis://redis:6379/0"
    signal_service_url: str = Field(default_factory=default_signal_service_url)
    graph_service_url: str = Field(default_factory=default_graph_service_url)

    # --- Phase 2: Controlled Scheduled Capture (all OFF by default) ---
    scheduled_capture_enabled: bool = False
    scheduled_capture_sources: str = "boshow"       # comma-separated allow-list of sources
    scheduled_capture_city_allowlist: str = ""      # comma-separated; empty = all cities
    scheduled_capture_max_tracked: int = 500        # cap on enrolled events
    scheduled_capture_max_jobs: int = 25            # max captures per run
    scheduled_capture_high_priority_limit: int = 50
    scheduled_capture_lock_ttl_seconds: int = 300   # lease duration for a claimed job
    scheduled_capture_max_attempts: int = 6         # retry budget before terminal
    scheduled_capture_parser_retry_limit: int = 2   # parser/invalid before manual review
    scheduled_capture_backoff_base_seconds: int = 120
    scheduled_capture_backoff_max_seconds: int = 21600  # 6h cap
    capture_http_timeout_seconds: float = 30.0

    # --- cadence bands (hours); overridable but sensible India-first defaults ---
    cadence_far_future_hours: int = 24     # not on sale / >30 days away
    cadence_mid_hours: int = 12            # 15-30 days away
    cadence_final_hours: int = 4           # final 14 days
    cadence_onsale_burst_hours: int = 2    # first 48h after on-sale (when known)
    cadence_event_day_hours: int = 2

    @property
    def scheduled_capture_source_set(self) -> frozenset[str]:
        return frozenset(s.strip() for s in self.scheduled_capture_sources.split(",") if s.strip())

    @property
    def city_allowlist_set(self) -> frozenset[str]:
        return frozenset(c.strip().lower() for c in self.scheduled_capture_city_allowlist.split(",") if c.strip())


settings = Settings()
