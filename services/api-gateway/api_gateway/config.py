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
    "artist_intelligence": "http://artist-intelligence-service:8010",
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
    "artist_intelligence": "http://localhost:8010",
}


def detect_network_mode() -> NetworkMode:
    explicit = os.environ.get("NQUARK_NETWORK_MODE", "").lower()
    if explicit in ("local", "docker"):
        return explicit  # type: ignore[return-value]
    if Path("/.dockerenv").exists():
        return "docker"
    return "local"


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
    # Cloud (Fly Managed Postgres) provides DATABASE_URL (pooled). Local Docker/dev keep the compose default.
    return normalize_db_url(os.environ.get("DATABASE_URL")) or \
        "postgresql+psycopg://nquark:nquark@postgres:5432/nquark"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NQUARK_", env_file=".env", extra="ignore")

    service_name: str = "api-gateway"
    port: int = 8000
    log_level: str = "info"
    network_mode: NetworkMode = Field(default_factory=detect_network_mode)

    # --- Admin Phase A: internal observability console (all OFF by default) ---
    admin_api_enabled: bool = False           # gate the /admin/v1 BFF surface
    admin_frontend_enabled: bool = False       # informational (frontend build gate)
    admin_dev_auth_enabled: bool = False       # isolated dev login; NEVER a prod identity provider
    admin_operational_actions_enabled: bool = False  # OPERATOR mutations (capture/enrich/resolve one event)
    # --- Admin Phase C: local-only inspection console ---
    # When on, the console runs unauthenticated under a single fixed INTERNAL_USER context (no login,
    # no roles). This is a LOCAL-DEVELOPMENT convenience only and must never be enabled on a public
    # cloud deployment — see docs/deployment.md. It still requires admin_api_enabled to be true.
    admin_local_mode: bool = False
    admin_session_secret: str = "dev-insecure-change-me"  # HMAC signing key for sessions (SET IN PROD)
    admin_session_ttl_seconds: int = 28800     # 8h session

    # --- Admin D: authenticated public production console (Google Workspace OIDC) ---
    # When oidc_enabled and NOT admin_local_mode, the console requires a Google sign-in whose verified
    # email is in the allowed Workspace domain (or the extra allowlist). A signed httpOnly session cookie
    # carries the principal. All authenticated users get a single read-only role (VIEWER); operational
    # mutations stay gated by admin_operational_actions_enabled (default off) — the deployed console is
    # operationally read-only. See docs/deployment.md.
    oidc_enabled: bool = False
    oidc_client_id: str | None = None          # Google OAuth 2.0 client id (fly secret in prod)
    oidc_client_secret: str | None = None      # Google OAuth 2.0 client secret (fly secret in prod)
    oidc_allowed_domain: str | None = None     # Google Workspace hosted domain (hd) required to sign in
    oidc_allowed_emails: str = ""              # optional comma-separated extra allowlist
    public_base_url: str | None = None         # e.g. https://nquark-admin.fly.dev (for the OIDC redirect)
    session_cookie_name: str = "nq_admin_session"
    session_cookie_secure: bool = True         # False only for local http testing
    # If set and the directory exists, the gateway serves the built SPA at / (single-app deployment).
    admin_frontend_dir: str | None = None
    admin_graph_max_nodes: int = 150           # hard cap on subgraph size
    admin_graph_max_depth: int = 2             # hard cap on subgraph expansion depth
    admin_audit_db_url: str | None = None      # defaults to postgres_url; SQLite in tests
    postgres_url: str = Field(default_factory=default_postgres_url)
    redis_url: str = "redis://redis:6379/0"
    neo4j_url: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "nquark"
    qdrant_url: str = "http://qdrant:6333"
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "nquark"
    minio_secret_key: str = "nquark"

    # Per-service upstream overrides. Unset -> the compose/local maps below are used unchanged.
    # Set these to deploy where service discovery differs from Docker Compose — e.g. on Fly:
    # NQUARK_GRAPH_SERVICE_URL=http://graph-service.flycast
    crawl_service_url: str | None = None
    media_service_url: str | None = None
    signal_service_url: str | None = None
    observation_service_url: str | None = None
    entity_service_url: str | None = None
    graph_service_url: str | None = None
    analytics_service_url: str | None = None
    feature_service_url: str | None = None
    intelligence_service_url: str | None = None
    artist_intelligence_service_url: str | None = None

    @property
    def migration_database_url(self) -> str:
        """DB URL for Alembic/startup migrations: MIGRATION_DATABASE_URL (direct) if set, else the app URL
        (admin_audit_db_url override, else postgres_url)."""
        return (normalize_db_url(os.environ.get("MIGRATION_DATABASE_URL"))
                or self.admin_audit_db_url or self.postgres_url)

    @property
    def downstream_services(self) -> dict[str, str]:
        base = dict(
            DOCKER_DOWNSTREAM_SERVICES
            if self.network_mode == "docker"
            else LOCAL_DOWNSTREAM_SERVICES
        )
        overrides = {
            "crawl": self.crawl_service_url,
            "media": self.media_service_url,
            "signal": self.signal_service_url,
            "observation": self.observation_service_url,
            "entity": self.entity_service_url,
            "graph": self.graph_service_url,
            "analytics": self.analytics_service_url,
            "feature": self.feature_service_url,
            "intelligence": self.intelligence_service_url,
            "artist_intelligence": self.artist_intelligence_service_url,
        }
        for key, url in overrides.items():
            if url:
                base[key] = url.rstrip("/")
        return base


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
