"""Phase 4D — deployment config + collector: DB URL selection, cloud source flags, collector cycle."""

from crawl_service import collector as coll
from crawl_service.config import Settings, default_postgres_url, normalize_db_url


# ---- DB URL normalization + migration selection ---------------------------------------------
def test_normalize_db_url_maps_to_psycopg():
    assert normalize_db_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert normalize_db_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_db_url("postgresql+psycopg://x") == "postgresql+psycopg://x"  # already normalized
    assert normalize_db_url(None) is None and normalize_db_url("") is None


def test_default_postgres_url_prefers_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@pooled:5432/db")
    assert default_postgres_url() == "postgresql+psycopg://u:p@pooled:5432/db"
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_migration_url_selection(monkeypatch):
    monkeypatch.setenv("NQUARK_POSTGRES_URL", "postgresql+psycopg://app@pooled/db")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgres://mig@direct:5432/db")
    s = Settings()
    assert s.postgres_url == "postgresql+psycopg://app@pooled/db"
    assert s.migration_database_url == "postgresql+psycopg://mig@direct:5432/db"  # direct, normalized
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    assert Settings().migration_database_url == "postgresql+psycopg://app@pooled/db"  # falls back


# ---- cloud source flags ----------------------------------------------------------------------
def test_collector_source_set_excludes_skillbox():
    s = Settings(collector_sources="boshow,district,skillbox")
    assert s.collector_source_set == frozenset({"boshow", "district"})


def test_collector_disabled_by_default():
    assert Settings().collector_enabled is False


# ---- collector cycle -------------------------------------------------------------------------
class _FakeScheduler:
    def __init__(self):
        self.enrolled = {}
        self.captured = 0

    def sync_from_refs(self, source, refs):
        self.enrolled[source] = list(refs)
        return len(refs)

    async def run_once(self, worker_id, *, trace=False):
        self.captured += 1
        return {"claimed": 0, "worker": worker_id}


async def test_run_cycle_discovers_and_captures(monkeypatch):
    async def fake_discover(source, limit):
        return [f"{source}-1", f"{source}-2"]
    monkeypatch.setattr(coll, "discover_refs", fake_discover)
    sched = _FakeScheduler()
    out = await coll.run_cycle(sched, sources=["boshow", "district"], discovery_limit=5, worker_id="t")
    assert out["discovery"]["boshow"]["enrolled"] == 2
    assert out["discovery"]["district"]["discovered"] == 2
    assert sched.captured == 1 and set(sched.enrolled) == {"boshow", "district"}


async def test_run_cycle_isolates_one_source_failure(monkeypatch):
    async def fake_discover(source, limit):
        if source == "boshow":
            raise RuntimeError("boshow discovery down")
        return ["district-1"]
    monkeypatch.setattr(coll, "discover_refs", fake_discover)
    sched = _FakeScheduler()
    out = await coll.run_cycle(sched, sources=["boshow", "district"], discovery_limit=5, worker_id="t")
    assert "error" in out["discovery"]["boshow"]           # one source failed
    assert out["discovery"]["district"]["enrolled"] == 1   # the other still enrolled
    assert sched.captured == 1                              # capture still ran


async def test_run_cycle_capture_only(monkeypatch):
    sched = _FakeScheduler()
    out = await coll.run_cycle(sched, sources=["boshow"], discovery_limit=5, worker_id="t", discover=False)
    assert out["discovery"] == {} and sched.captured == 1
