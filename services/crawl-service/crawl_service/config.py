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

    # --- Phase 2.1: evidence-based capture enrichment (all OFF by default) ---
    capture_enrichment_enabled: bool = False
    capture_enrichment_sources: str = "boshow"
    capture_enrichment_public_page_enabled: bool = False  # fetch the source public page for candidates
    capture_enrichment_min_confidence: float = 0.6        # min confidence to auto-resolve/update scheduler

    # --- Phase 2.2: live public-page enrichment pilot (all OFF by default) ---
    capture_enrichment_pilot_enabled: bool = False
    capture_enrichment_pilot_max_events: int = 100
    capture_enrichment_pilot_sample_seed: int = 1
    capture_enrichment_pilot_priority_only: bool = False
    capture_enrichment_pilot_min_tracked_age_hours: int = 0
    capture_enrichment_pilot_max_requests_per_run: int = 100
    capture_enrichment_public_page_timeout: float = 15.0
    capture_enrichment_public_page_rate_limit_ms: int = 500   # min gap between page requests
    boshow_share_base: str = "https://www.boshow.in/api/shows/share"

    # --- Phase 3.1: cross-inventory entity resolution (all OFF by default) ---
    entity_resolution_enabled: bool = False
    entity_resolution_sources: str = "boshow,district"
    artist_auto_resolve_threshold: float = 0.8
    venue_auto_resolve_threshold: float = 0.8
    organizer_auto_resolve_threshold: float = 0.75
    event_series_auto_resolve_threshold: float = 0.7
    entity_resolution_max_events_per_run: int = 500

    # --- Phase 4B: best-effort creative-asset observation hook (OFF by default) ---
    # When on, a successful capture notifies media-service to observe the event's creative. It is
    # best-effort and never fails the capture or Shadow Ledger update.
    media_observation_enabled: bool = False
    media_service_url: str = Field(default_factory=lambda: (
        "http://media-service:8002" if detect_network_mode() == "docker" else "http://localhost:8002"))
    media_observation_sources: str = "boshow,district"

    # --- Phase 4D: continuous in-process collector (periodic discover+enroll+capture; OFF by default) ---
    # The smallest bounded, restart-safe mechanism to keep a Fly collector both enrolling new events and
    # capturing tracked ones — it reuses the existing scheduler (sync_from_refs + run_once), no second
    # scheduling architecture. All state lives in Postgres, so a restart just resumes idempotently.
    collector_enabled: bool = False
    collector_sources: str = "boshow,district"        # Skillbox intentionally excluded
    collector_discovery_interval_seconds: int = 3600  # how often to discover/enroll new events
    collector_capture_interval_seconds: int = 300     # how often to run a capture pass
    collector_discovery_limit: int = 50               # bounded discovery per source per cycle

    # --- Phase 3: independent second source + cross-platform reconciliation (all OFF by default) ---
    second_source_capture_enabled: bool = False
    second_source_name: str = "district"
    reconciliation_enabled: bool = False
    reconciliation_auto_match_threshold: float = 0.75
    reconciliation_possible_match_threshold: float = 0.5
    reconciliation_date_tolerance_hours: int = 36   # same day ±1 for tz/source-format slack
    reconciliation_max_pairs_per_run: int = 500
    # Decision thresholds (deterministic; configurable).
    pilot_min_retrieval_success: float = 0.7
    pilot_min_incremental_gain_rate: float = 0.1
    pilot_max_conflict_rate: float = 0.2
    pilot_min_parser_success: float = 0.7

    # --- cadence bands (hours); overridable but sensible India-first defaults ---
    cadence_far_future_hours: int = 24     # not on sale / >30 days away
    cadence_mid_hours: int = 12            # 15-30 days away
    cadence_final_hours: int = 4           # final 14 days
    cadence_onsale_burst_hours: int = 2    # first 48h after on-sale (when known)
    cadence_event_day_hours: int = 2

    def _with_second_source(self, base: frozenset[str]) -> frozenset[str]:
        if self.second_source_capture_enabled and self.second_source_name:
            return base | {self.second_source_name}
        return base

    @property
    def scheduled_capture_source_set(self) -> frozenset[str]:
        base = frozenset(s.strip() for s in self.scheduled_capture_sources.split(",") if s.strip())
        return self._with_second_source(base)

    @property
    def capture_enrichment_source_set(self) -> frozenset[str]:
        base = frozenset(s.strip() for s in self.capture_enrichment_sources.split(",") if s.strip())
        return self._with_second_source(base)

    @property
    def entity_resolution_source_set(self) -> frozenset[str]:
        base = frozenset(s.strip() for s in self.entity_resolution_sources.split(",") if s.strip())
        return self._with_second_source(base)

    @property
    def media_observation_source_set(self) -> frozenset[str]:
        return frozenset(s.strip() for s in self.media_observation_sources.split(",") if s.strip())

    @property
    def collector_source_set(self) -> frozenset[str]:
        # Skillbox is never collected by the continuous loop, even if it appears in the list.
        return frozenset(s.strip() for s in self.collector_sources.split(",")
                         if s.strip() and s.strip() != "skillbox")

    @property
    def migration_database_url(self) -> str:
        """DB URL for Alembic/startup migrations: MIGRATION_DATABASE_URL if set, else the app URL."""
        return normalize_db_url(os.environ.get("MIGRATION_DATABASE_URL")) or self.postgres_url

    def entity_auto_threshold(self, entity_type: str) -> float:
        return {
            "ARTIST": self.artist_auto_resolve_threshold,
            "VENUE": self.venue_auto_resolve_threshold,
            "ORGANIZER": self.organizer_auto_resolve_threshold,
            "EVENT_SERIES": self.event_series_auto_resolve_threshold,
        }.get(entity_type, 0.8)

    @property
    def city_allowlist_set(self) -> frozenset[str]:
        return frozenset(c.strip().lower() for c in self.scheduled_capture_city_allowlist.split(",") if c.strip())


settings = Settings()
