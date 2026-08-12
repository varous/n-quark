"""Phase 5A.3.1 — candidate promotion, ecosystem discovery, dynamic quota allocation, live event cadence.

Canonical ownership stays in crawl (FakeCrawl stands in for the crawl-owned create/match path); the demand
service never writes a canonical id to its own DB.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from artist_intelligence_service import candidates as cand
from artist_intelligence_service import config, promotion, quota
from artist_intelligence_service.discovery import run_ecosystem_discovery, run_youtube_discovery
from artist_intelligence_service.models import (
    ArtistCandidate,
    ArtistExternalIdentity,
    DemandRefreshJob,
)
from artist_intelligence_service.providers.youtube import YouTubeProvider
from artist_intelligence_service.scheduler import JOB_CHANNEL, JOB_IDENTITY, DemandScheduler
from artist_intelligence_service.service import DemandService
from tests.conftest import FakeSignal, candidate


class FakeCrawl:
    """Stands in for the crawl-owned entity path (create/match). Records create calls.

    ``registered`` models the authoritative entity-resolution registry: an existing canonical and any
    id created here are registry-backed; ``canonical_artist_registered`` answers the 5B.1.1 membership
    check. Pass ``registered={...}`` to model an id that is referenced but NOT in the registry (orphan)."""

    def __init__(self, existing: dict[str, str] | None = None, registered: set[str] | None = None):
        import re
        self._re = re
        self.existing = {self._n(k): v for k, v in (existing or {}).items()}
        self.create_calls: list[str] = []
        self.registered = set(self.existing.values()) if registered is None else set(registered)

    def _n(self, s: str) -> str:
        return self._re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

    async def find_artist_by_name(self, name):
        cid = self.existing.get(self._n(name))
        return {"canonical_entity_id": cid, "canonical_name": name} if cid else None

    async def create_artist(self, name, *, provenance=None, source=None):
        self.create_calls.append(name)
        cid = "artist:" + self._re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        self.registered.add(cid)  # crawl-owned creation registers it → enumerable/acknowledged
        return {"canonical_entity_id": cid, "entity_type": "ARTIST", "created": True,
                "registry_registered": True}

    async def canonical_artist_registered(self, cid):
        return cid in self.registered

    async def artists(self, *, limit=200, offset=0):
        return []


class FakeGraph:
    def __init__(self, *, events=None, fail=False):
        self._events = events or []
        self.fail = fail
        self.base_url = "http://fake-graph"

    async def neighbors(self, node_id, *, direction="both", relationship=None):
        if self.fail:
            raise RuntimeError("graph down")
        return self._events


def _svc():
    return DemandService(youtube=YouTubeProvider(signal=FakeSignal(
        search={"arijit singh": [candidate("UC_real", "Arijit Singh", topic=True)]},
        channel={"UC_real": {"subscriber_count": 1, "total_view_count": 1, "video_count": 1}})))


# ---- candidate promotion -----------------------------------------------------------------------
async def test_youtube_only_weak_candidate_does_not_canonicalize(db):
    c, _ = cand.upsert_candidate(db, display_name="Some Indie Act",
                                 discovery_source=cand.SRC_YOUTUBE_SEARCH, discovery_source_id="UC_x")
    crawl = FakeCrawl()
    out = await promotion.promote(db, c, crawl=crawl, scheduler=DemandScheduler(service=_svc()))
    assert out["promoted"] is False
    assert c.canonical_artist_id is None and c.status == cand.RESOLUTION_PENDING
    assert crawl.create_calls == []                       # weak evidence never creates canonical


async def test_candidate_matches_existing_canonical(db):
    c, _ = cand.upsert_candidate(db, display_name="Arijit Singh",
                                 discovery_source=cand.SRC_YOUTUBE_SEARCH, discovery_source_id="UC_a")
    crawl = FakeCrawl(existing={"Arijit Singh": "artist:arijit-singh"})
    out = await promotion.promote(db, c, crawl=crawl, scheduler=DemandScheduler(service=_svc()))
    assert out["promoted"] and out["method"] == promotion.MATCH_EXISTING_CANONICAL
    assert c.canonical_artist_id == "artist:arijit-singh" and c.status == cand.RESOLVED
    assert crawl.create_calls == []                       # matched, not created


async def test_multi_source_promotes_through_owner(db):
    # same artist from two independent sources → create via the crawl owner
    cand.upsert_candidate(db, display_name="New Band", discovery_source=cand.SRC_EVENT,
                          discovery_source_id="ev1")
    c, _ = cand.upsert_candidate(db, display_name="New Band",
                                 discovery_source=cand.SRC_YOUTUBE_SEARCH, discovery_source_id="UC_nb")
    crawl = FakeCrawl()
    out = await promotion.promote(db, c, crawl=crawl, scheduler=DemandScheduler(service=_svc()))
    assert out["promoted"] and out["method"] == promotion.MULTI_SOURCE_CONFIRMED
    assert out["created_canonical"] is True
    assert crawl.create_calls == ["New Band"]            # creation routed through crawl owner
    assert c.canonical_artist_id == "artist:new-band"


async def test_promotion_queues_identity_resolution(db):
    c, _ = cand.upsert_candidate(db, display_name="Arijit Singh",
                                 discovery_source=cand.SRC_YOUTUBE_SEARCH, discovery_source_id="UC_a")
    crawl = FakeCrawl(existing={"Arijit Singh": "artist:arijit-singh"})
    await promotion.promote(db, c, crawl=crawl, scheduler=DemandScheduler(service=_svc()))
    assert db.execute(select(func.count()).select_from(DemandRefreshJob)
                      .where(DemandRefreshJob.job_type == JOB_IDENTITY)).scalar_one() == 1


async def test_promotion_is_idempotent(db):
    c, _ = cand.upsert_candidate(db, display_name="Arijit Singh",
                                 discovery_source=cand.SRC_YOUTUBE_SEARCH, discovery_source_id="UC_a")
    crawl = FakeCrawl(existing={"Arijit Singh": "artist:arijit-singh"})
    sched = DemandScheduler(service=_svc())
    await promotion.promote(db, c, crawl=crawl, scheduler=sched)
    await promotion.promote(db, c, crawl=crawl, scheduler=sched)
    assert db.execute(select(func.count()).select_from(DemandRefreshJob)
                      .where(DemandRefreshJob.job_type == JOB_IDENTITY)).scalar_one() == 1
    assert c.canonical_artist_id == "artist:arijit-singh"


async def test_backlog_processing_is_bounded(db):
    for i in range(5):
        cand.upsert_candidate(db, display_name=f"Act {i}", discovery_source=cand.SRC_YOUTUBE_SEARCH,
                              discovery_source_id=f"UC_{i}")
    out = await promotion.process_backlog(db, crawl=FakeCrawl(), scheduler=DemandScheduler(service=_svc()),
                                          limit=2)
    assert out["evaluated"] == 2 and out["batch_limit"] == 2


async def test_no_work_spends_no_quota(db):
    # empty backlog → no crawl create/match traffic, nothing promoted
    crawl = FakeCrawl()
    out = await promotion.process_backlog(db, crawl=crawl, scheduler=DemandScheduler(service=_svc()))
    assert out["evaluated"] == 0 and out["promoted"] == 0 and crawl.create_calls == []


# ---- ecosystem discovery -----------------------------------------------------------------------
def _ecosystem_signal():
    return FakeSignal(videos={"UCseed": [
        {"video_id": f"v{i}", "title": f"Festival set {i}", "published_at": "2026-08-01T00:00:00Z",
         "views": 1, "likes": 1, "comments": 1} for i in range(10)]})


async def test_ecosystem_discovery_creates_candidates_only(db, monkeypatch):
    monkeypatch.setattr(config.settings, "youtube_ecosystem_enabled", True)
    monkeypatch.setattr(config.settings, "youtube_ecosystem_seed_channels", "UCseed")
    out = await run_ecosystem_discovery(db, provider=YouTubeProvider(signal=_ecosystem_signal()))
    assert out["status"] == "OK" and out["candidates_created"] >= 1
    rows = db.execute(select(ArtistCandidate)).scalars().all()
    assert rows and all(r.discovery_source == cand.SRC_YOUTUBE_ECOSYSTEM for r in rows)
    assert db.execute(select(func.count()).select_from(ArtistExternalIdentity)).scalar_one() == 0


async def test_ecosystem_respects_size_limit(db, monkeypatch):
    monkeypatch.setattr(config.settings, "youtube_ecosystem_enabled", True)
    monkeypatch.setattr(config.settings, "youtube_ecosystem_seed_channels", "UCseed")
    monkeypatch.setattr(config.settings, "youtube_ecosystem_max_candidates_per_run", 3)
    out = await run_ecosystem_discovery(db, provider=YouTubeProvider(signal=_ecosystem_signal()))
    assert out["candidates_created"] <= 3


# ---- dynamic search-quota allocation -----------------------------------------------------------
def _fill_purpose(db, purpose, searches):
    m = quota.QuotaMeter()
    for _ in range(searches):
        m.search(purpose=purpose)
    quota.record_meter(db, "YOUTUBE", m)


def test_search_allocation_enforced_by_purpose(db, monkeypatch):
    monkeypatch.setattr(config.settings, "youtube_search_allocation_enforced", True)
    # discovery slice = 0.35*10000*0.40 = 1400 units = 14 searches; fill it, 15th over slice
    _fill_purpose(db, quota.SEARCH_DISCOVERY, 14)
    assert quota.search_purpose_used(db, "YOUTUBE", quota.SEARCH_DISCOVERY) == 1400
    # over slice + others still have backlog → refused
    assert quota.can_spend_search(db, "YOUTUBE", quota.SEARCH_DISCOVERY, 100,
                                  others_have_backlog=True) is False


def test_unused_allocation_transfers(db, monkeypatch):
    monkeypatch.setattr(config.settings, "youtube_search_allocation_enforced", True)
    _fill_purpose(db, quota.SEARCH_DISCOVERY, 14)          # own slice spent
    # over slice but others idle → may borrow (bucket + reserve still hold)
    assert quota.can_spend_search(db, "YOUTUBE", quota.SEARCH_DISCOVERY, 100,
                                  others_have_backlog=False) is True


def test_reserve_cannot_be_consumed(db, monkeypatch):
    # tiny budget so the global usable cap (reserve) bites before any purpose slice
    monkeypatch.setattr(config.settings, "youtube_daily_quota_units", 1000)
    monkeypatch.setattr(config.settings, "youtube_quota_target_utilization", 0.2)  # usable=200
    m = quota.QuotaMeter()
    m.read(units=200)                                      # consume 200 general-read units
    quota.record_meter(db, "YOUTUBE", m)
    # even borrowing / near reset cannot cross the usable cap (reserve stays protected)
    assert quota.can_spend_search(db, "YOUTUBE", quota.SEARCH_UNRESOLVED, 100,
                                  others_have_backlog=False, near_reset=True) is False


def test_near_reset_saturation(db, monkeypatch):
    monkeypatch.setattr(config.settings, "youtube_search_allocation_enforced", True)
    _fill_purpose(db, quota.SEARCH_DISCOVERY, 14)          # slice spent
    # over slice, others busy, but near reset → allowed to use otherwise-idle budget
    assert quota.can_spend_search(db, "YOUTUBE", quota.SEARCH_DISCOVERY, 100,
                                  others_have_backlog=True, near_reset=True) is True


# ---- live event-proximity cadence --------------------------------------------------------------
async def test_event_proximity_from_graph(db, monkeypatch):
    monkeypatch.setattr(config.settings, "event_aware_cadence_enabled", True)
    start = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    graph = FakeGraph(events=[{"node": {"id": "event:x", "properties": {"starts_at": start}}}])
    sched = DemandScheduler(service=_svc(), graph=graph)
    days = await sched._event_proximity_days("artist:x")
    assert days is not None and 4.5 <= days <= 5.5


def test_upcoming_event_increases_cadence(db):
    sched = DemandScheduler(service=_svc())
    now = datetime.now(UTC)
    job = DemandRefreshJob(id="j1", dedup_key="d1", canonical_artist_id="artist:x",
                           provider="YOUTUBE", job_type=JOB_CHANNEL, status="RUNNING",
                           scheduled_at=now, attempt_count=1, priority=20, detail={},
                           created_at=now, updated_at=now)
    far = sched._next_refresh_for(db, job, now, days_to_event=None)
    near = sched._next_refresh_for(db, job, now, days_to_event=2.0)   # T-2 → tight band
    assert near < far                                     # event proximity shortens the interval


async def test_graph_failure_degrades_to_normal_cadence(db, monkeypatch):
    monkeypatch.setattr(config.settings, "event_aware_cadence_enabled", True)
    sched = DemandScheduler(service=_svc(), graph=FakeGraph(fail=True))
    days = await sched._event_proximity_days("artist:x")
    assert days is None                                   # safe degrade, no exception
