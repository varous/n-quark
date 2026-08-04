"""Gateway DB engine/session + migration-version introspection.

Production schema is Alembic-managed. A SQLite dev/test URL (isolated) creates tables from metadata so
the console runs offline; a Postgres URL never auto-creates in production — it must be migrated.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from api_gateway.config import settings
from api_gateway.db.models import Base

VERSION_TABLE = "alembic_version_gateway"

_engine: Engine | None = None
_Session: sessionmaker | None = None


def _db_url() -> str:
    return settings.admin_audit_db_url or settings.postgres_url


def _make_engine(url: str) -> Engine | None:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    try:
        engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
        with engine.connect():
            pass
        return engine
    except Exception:  # noqa: BLE001 — caller falls back
        return None


def get_engine() -> Engine:
    global _engine, _Session
    if _engine is not None:
        return _engine
    url = _db_url()
    engine = _make_engine(url)
    if engine is None:  # isolated dev/offline fallback
        url = "sqlite:////tmp/nquark_admin_gateway.db"
        engine = _make_engine(url)
    assert engine is not None
    # SQLite (dev/test) is created from metadata; Postgres is owned by Alembic and never auto-created.
    if url.startswith("sqlite"):
        Base.metadata.create_all(engine)
    _engine = engine
    _Session = sessionmaker(bind=engine, expire_on_commit=False)
    return engine


def get_session() -> sessionmaker:
    if _Session is None:
        get_engine()
    assert _Session is not None
    return _Session


def migration_status() -> dict[str, object]:
    """Report the applied gateway migration version + whether the expected tables exist."""
    engine = get_engine()
    dialect = engine.dialect.name
    version: str | None = None
    try:
        with engine.connect() as c:
            row = c.execute(text(f"SELECT version_num FROM {VERSION_TABLE}")).first()
            version = row[0] if row else None
    except Exception:  # noqa: BLE001 — version table absent (sqlite dev, or unmigrated)
        version = None
    tables = set(Base.metadata.tables)
    try:
        from sqlalchemy import inspect
        present = set(inspect(engine).get_table_names())
    except Exception:  # noqa: BLE001
        present = set()
    missing = sorted(tables - present)
    managed = dialect != "sqlite"
    ok = (not missing) and (version is not None or not managed)
    return {"dialect": dialect, "alembic_managed": managed, "version": version,
            "tables_present": sorted(tables & present), "tables_missing": missing, "ok": ok}
