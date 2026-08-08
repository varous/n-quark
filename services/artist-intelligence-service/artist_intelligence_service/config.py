import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Version-controlled default India-market discovery queries (evolve via config/env, not code edits).
# Deliberately India-oriented and genre/region/format-diverse; bounded per run by youtube_discovery_per_run.
DEFAULT_DISCOVERY_QUERIES: tuple[str, ...] = (
    "Indian indie artist live",
    "Bollywood playback singer",
    "Punjabi live concert India",
    "Tamil music live performance",
    "Telugu music concert",
    "Bengali band live Kolkata",
    "Indian classical fusion live",
    "India music festival lineup",
    "Hindi rap live India",
    "Malayalam music live",
    "Marathi live music",
    "Indian electronic music festival",
)


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

    # --- Phase 5A.3: artist universe & demand saturation (all OFF by default). ---
    # Automatic onboarding: a canonical ARTIST without a YouTube identity is enqueued for identity
    # discovery (no manual operator call). Backfill enumerates the existing cohort the same way.
    artist_auto_onboard_enabled: bool = False
    artist_backfill_batch_size: int = 50         # canonical artists enqueued per backfill pass
    # Independent YouTube artist discovery (candidates only — never canonical artists).
    youtube_discovery_enabled: bool = False
    youtube_discovery_per_run: int = 3           # bounded discovery queries per scheduler pass
    youtube_discovery_results_per_query: int = 10
    # India-market-oriented default discovery queries (overridable; not hardcoded in business logic).
    # Comma or newline separated; empty => the DEFAULT_DISCOVERY_QUERIES constant below is used.
    youtube_discovery_queries: str = ""

    # --- Phase 5A.3: YouTube quota buckets (configurable; Google's real model, not one synthetic pool). ---
    # YouTube Data API v3 default project budget is 10,000 units/day, reset at midnight PROVIDER-TZ.
    youtube_daily_quota_units: int = 10000
    youtube_quota_reset_tz: str = "America/Los_Angeles"   # YouTube quota resets midnight Pacific, not UTC
    youtube_quota_target_utilization: float = 0.95        # intentionally use most of the budget…
    youtube_quota_reserve_fraction: float = 0.05          # …while keeping an operational reserve
    # Bucket allocation as a fraction of the daily budget (sum ≤ target utilization).
    youtube_bucket_fraction_search: float = 0.35
    youtube_bucket_fraction_general_read: float = 0.45
    youtube_bucket_fraction_video_batch: float = 0.15
    # Search sub-allocation (of the SEARCH bucket) across purposes; discovery grows as the backlog drains.
    youtube_search_alloc_unresolved: float = 0.40
    youtube_search_alloc_discovery: float = 0.40
    youtube_search_alloc_ambiguity: float = 0.15
    youtube_search_alloc_reserve: float = 0.05

    # --- Phase 5A.3.1: candidate promotion (route canonical creation through the crawl entity owner). ---
    candidate_promotion_enabled: bool = False
    candidate_promotion_batch_size: int = 25         # bounded candidates evaluated per pass
    candidate_promotion_min_sources: int = 2         # ≥N independent discovery sources → MULTI_SOURCE_CONFIRMED
    # --- Phase 5A.3.1: bounded YouTube ecosystem discovery (seed channels → uploads → candidates). ---
    youtube_ecosystem_enabled: bool = False
    youtube_ecosystem_seed_channels: str = ""        # comma/newline channel ids (festival/promoter/venue/media/label)
    youtube_ecosystem_max_seeds_per_run: int = 2
    youtube_ecosystem_max_videos_per_seed: int = 10
    youtube_ecosystem_max_candidates_per_run: int = 25
    # --- Phase 5A.3.1: dynamic search allocation + live event-proximity cadence. ---
    youtube_search_allocation_enforced: bool = True  # honor per-purpose splits, borrow unused, keep reserve
    event_aware_cadence_enabled: bool = False        # read nearest upcoming Indian event from the graph
    event_proximity_max_days: int = 90               # ignore events further out than this

    # --- Phase 5A.3: temporal resolution + adaptive cadence (seconds; configurable, not hardcoded). ---
    youtube_hourly_observations: bool = True      # YouTube live metrics bucket by HOUR (Trends stays daily)
    youtube_catalogue_backfill_depth: int = 50    # bounded one-time uploads registry backfill per channel
    # Channel refresh cadence by activity class.
    cadence_channel_event_imminent_s: int = 4 * 3600
    cadence_channel_active_s: int = 6 * 3600
    cadence_channel_standard_s: int = 86400
    cadence_channel_longtail_s: int = 3 * 86400
    # Video refresh cadence by upload age.
    cadence_video_fresh_s: int = 3600             # 0–72h
    cadence_video_recent_s: int = 4 * 3600        # 3–14d
    cadence_video_mature_s: int = 86400           # 15–90d
    cadence_video_old_s: int = 7 * 86400          # >90d
    # Event-aware artist cadence by T-relative band (days before the event → seconds between refreshes).
    cadence_event_t60_s: int = 86400              # T-60 … T-30
    cadence_event_t30_s: int = 6 * 3600           # T-30 … T-14
    cadence_event_t14_s: int = 4 * 3600           # T-14 … T-3
    cadence_event_t3_s: int = 2 * 3600            # T-3 … T
    cadence_event_post_s: int = 2 * 3600          # T … T+3 (high frequency)

    # --- Deterministic read-model thresholds (transparent + configurable). ---
    demand_min_observations_for_delta: int = 2   # below this: INSUFFICIENT_HISTORY
    demand_freshness_stale_hours: int = 48       # observation older than this is "stale"
    geo_affinity_high_interest_threshold: int = 60   # relative to analysed cohort (0-100 scale)
    geo_affinity_low_interest_threshold: int = 25
    demand_cohort_max_artists: int = 30          # bounded pilot cohort size

    @property
    def discovery_queries(self) -> list[str]:
        """Effective India-market discovery queries — operator override, else the version-controlled
        default set. Not a permanent hardcoded list in business logic; edit config/env to evolve it."""
        raw = (self.youtube_discovery_queries or "").replace("\n", ",")
        override = [q.strip() for q in raw.split(",") if q.strip()]
        return override or list(DEFAULT_DISCOVERY_QUERIES)

    @property
    def ecosystem_seed_channels(self) -> list[str]:
        """Operator-configured YouTube ecosystem seed channel ids (festival/promoter/venue/media/label).
        Empty by default — ecosystem discovery is a no-op until seeds are provided."""
        raw = (self.youtube_ecosystem_seed_channels or "").replace("\n", ",")
        return [c.strip() for c in raw.split(",") if c.strip()]

    def youtube_quota_date(self, now: datetime | None = None):
        """Today's YouTube quota-day in the provider's reset timezone (midnight Pacific by default),
        NOT UTC — so accounting rolls over when Google's quota actually resets."""
        from datetime import datetime as _dt
        now = now or _dt.now(UTC)
        try:
            from zoneinfo import ZoneInfo
            return now.astimezone(ZoneInfo(self.youtube_quota_reset_tz)).date()
        except Exception:  # noqa: BLE001 — bad tz name must never break accounting; fall back to UTC
            return now.astimezone(UTC).date()

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
