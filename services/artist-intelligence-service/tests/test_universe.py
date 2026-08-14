"""Phase 5A.3 — artist universe & demand saturation.

Candidate layer, event-derived auto-onboarding, existing-artist backfill, independent YouTube discovery,
India market evidence, quota buckets + reserve + provider-tz reset, hourly observation idempotency,
catalogue backfill + video registry, batch-stat accounting, and identity-queue execution.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from artist_intelligence_service import candidates as cand
from artist_intelligence_service import config, quota, universe
from artist_intelligence_service.discovery import run_youtube_discovery
from artist_intelligence_service.models import (
    ArtistCandidate,
    ArtistExternalIdentity,
    ArtistMarketEvidence,
    DemandRefreshJob,
    YouTubeVideo,
)
from artist_intelligence_service.models import ArtistDemandObservation as ADO
from artist_intelligence_service.providers.base import PROVIDER_YOUTUBE, RESOLVED
from artist_intelligence_service.providers.youtube import YouTubeProvider
from artist_intelligence_service.scheduler import (
    JOB_CATALOGUE,
    JOB_IDENTITY,
    DemandScheduler,
)
from artist_intelligence_service.service import DemandService
from tests.conftest import FakeSignal, candidate

ARTIST = "artist:arijit-singh"
CID = "UC_real"


def _fake():
    return FakeSignal(
        search={"arijit singh": [candidate("UC_real", "Arijit Singh", topic=True)],
                "indian indie artist live": [candidate("UC_disc", "Some Indie Act", topic=True)]},
        channel={CID: {"subscriber_count": 31200000, "total_view_count": 9800000000, "video_count": 180}},
        videos={CID: [{"video_id": "v1", "views": 4200000, "likes": 310000, "comments": 12000,
                       "published_at": "2026-08-07T12:00:00Z"}]})


def _svc(fake=None):
    return DemandService(youtube=YouTubeProvider(signal=fake or _fake()))


# ---- candidate universe ------------------------------------------------------------------------
def test_onboard_queues_identity_discovery(db):
    svc = _svc()
    out = universe.onboard_artist(db, canonical_artist_id=ARTIST, display_name="Arijit Singh",
                                  scheduler=DemandScheduler(service=svc))
    assert out["identity_present"] is False
    assert out["identity_discovery_queued"] is True
    assert out["market_evidence_recorded"] is True
    job = db.execute(select(DemandRefreshJob).where(DemandRefreshJob.job_type == JOB_IDENTITY)).scalar_one()
    assert job.canonical_artist_id == ARTIST and job.status == "PENDING"
    # event-derived → CONFIRMED_LIVE_INDIA evidence with provenance preserved
    ev = db.execute(select(ArtistMarketEvidence)).scalar_one()
    assert ev.evidence_class == "CONFIRMED_LIVE_INDIA" and ev.provenance.get("onboarded_at")


def test_onboard_is_idempotent(db):
    svc = _svc()
    sched = DemandScheduler(service=svc)
    universe.onboard_artist(db, canonical_artist_id=ARTIST, display_name="Arijit Singh", scheduler=sched)
    universe.onboard_artist(db, canonical_artist_id=ARTIST, display_name="Arijit Singh", scheduler=sched)
    assert db.execute(select(func.count()).select_from(DemandRefreshJob)
                      .where(DemandRefreshJob.job_type == JOB_IDENTITY)).scalar_one() == 1
    assert db.execute(select(func.count()).select_from(ArtistCandidate)).scalar_one() == 1


async def test_backfill_queues_missing_identities(db):
    class FakeCrawl:
        async def artists(self, *, limit=200, offset=0):
            return [{"canonical_entity_id": "artist:a", "canonical_name": "A"},
                    {"canonical_entity_id": "artist:b", "canonical_name": "B"}]
    out = await universe.backfill_missing_identities(db, crawl=FakeCrawl(),
                                                     scheduler=DemandScheduler(service=_svc()))
    assert out["status"] == "OK" and out["queued"] == 2
    assert db.execute(select(func.count()).select_from(DemandRefreshJob)
                      .where(DemandRefreshJob.job_type == JOB_IDENTITY)).scalar_one() == 2


async def test_youtube_discovery_creates_candidate_not_canonical(db, monkeypatch):
    monkeypatch.setattr(config.settings, "youtube_discovery_enabled", True)
    monkeypatch.setattr(config.settings, "youtube_discovery_queries", "Indian indie artist live")
    monkeypatch.setattr(config.settings, "youtube_discovery_per_run", 1)
    out = await run_youtube_discovery(db, provider=YouTubeProvider(signal=_fake()))
    assert out["candidates_created"] == 1
    c = db.execute(select(ArtistCandidate)).scalar_one()
    assert c.discovery_source == cand.SRC_YOUTUBE_SEARCH and c.status == cand.NEW
    assert c.canonical_artist_id is None                      # never a canonical artist
    # no external identity was created by discovery
    assert db.execute(select(func.count()).select_from(ArtistExternalIdentity)).scalar_one() == 0


async def test_duplicate_candidate_merges(db, monkeypatch):
    monkeypatch.setattr(config.settings, "youtube_discovery_enabled", True)
    monkeypatch.setattr(config.settings, "youtube_discovery_queries", "Indian indie artist live")
    monkeypatch.setattr(config.settings, "youtube_discovery_per_run", 1)
    await run_youtube_discovery(db, provider=YouTubeProvider(signal=_fake()))
    await run_youtube_discovery(db, provider=YouTubeProvider(signal=_fake()))
    assert db.execute(select(func.count()).select_from(ArtistCandidate)).scalar_one() == 1  # no explosion
    c = db.execute(select(ArtistCandidate)).scalar_one()
    assert c.evidence_refs == 2


def test_candidate_resolves_to_existing_canonical(db):
    c, _ = cand.upsert_candidate(db, display_name="X", discovery_source=cand.SRC_YOUTUBE_SEARCH,
                                 discovery_source_id="UC_x")
    cand.link_to_canonical(db, c, "artist:x")
    assert c.status == cand.RESOLVED and c.canonical_artist_id == "artist:x"


def test_india_evidence_retains_provenance(db):
    _, created = cand.record_market_evidence(
        db, canonical_artist_id=ARTIST, evidence_class="INDIA_DEMAND_OBSERVED",
        source="GOOGLE_TRENDS", source_ref="IN-WB", provenance={"region": "West Bengal", "value": 100})
    assert created
    # idempotent on (artist, class, source, source_ref)
    _, again = cand.record_market_evidence(
        db, canonical_artist_id=ARTIST, evidence_class="INDIA_DEMAND_OBSERVED",
        source="GOOGLE_TRENDS", source_ref="IN-WB", provenance={"region": "West Bengal"})
    assert not again
    ev = db.execute(select(ArtistMarketEvidence)).scalar_one()
    assert ev.provenance["region"] == "West Bengal"


# ---- YouTube quota buckets / reset / reserve ---------------------------------------------------
async def test_search_partitioned_into_bucket(db):
    svc = _svc()
    await svc.resolve_youtube(db, ARTIST, query="Arijit Singh", hints={"provider_id": CID})
    snap = quota.bucket_snapshot(db, PROVIDER_YOUTUBE)
    assert snap["buckets"]["SEARCH"]["used"] == 1           # one search = 1 unit (independent quota)
    assert snap["buckets"]["GENERAL_READ"]["used"] >= 1     # the channels.list verify is a general read
    assert snap["reconciles"] is True                       # general total = GENERAL_READ + VIDEO_BATCH


def test_quota_reset_follows_provider_tz(monkeypatch):
    # a UTC instant that is the previous day in Pacific → the YouTube quota day must be the Pacific date
    monkeypatch.setattr(config.settings, "youtube_quota_reset_tz", "America/Los_Angeles")
    instant = datetime(2026, 8, 9, 3, 0, tzinfo=UTC)          # 2026-08-08 20:00 Pacific
    assert quota.quota_date_for(PROVIDER_YOUTUBE, now=instant).isoformat() == "2026-08-08"


def test_reserve_prevents_over_scheduling(db, monkeypatch):
    # 5B.2.7: SEARCH is capped by its own independent Search-Queries quota; at 0 calls/day it refuses
    monkeypatch.setattr(config.settings, "youtube_search_daily_calls", 0)
    assert quota.can_spend(db, PROVIDER_YOUTUBE, quota.BUCKET_SEARCH, quota.YT_SEARCH_UNITS) is False


async def test_quota_exhausted_defers_not_invalidates(db, monkeypatch):
    svc = _svc()
    # queue an identity job, then force the SEARCH budget to zero → the job must DEFER, not fail/invalidate
    DemandScheduler(service=svc).enqueue_identity_discovery(db, ARTIST, display_name="Arijit Singh")
    db.commit()
    monkeypatch.setattr(config.settings, "youtube_search_daily_calls", 0)   # independent SEARCH quota spent
    out = await DemandScheduler(service=svc).run_once(db)
    assert out["outcomes"].get("DEFERRED", 0) == 1
    job = db.execute(select(DemandRefreshJob).where(DemandRefreshJob.job_type == JOB_IDENTITY)).scalar_one()
    assert job.status == "PENDING" and job.result_code == "QUOTA_EXHAUSTED"


# ---- known-id refresh does not consume search ---------------------------------------------------
async def test_known_id_refresh_no_search(db):
    fake = _fake()
    svc = _svc(fake)
    await svc.resolve_youtube(db, ARTIST, query="Arijit Singh", hints={"provider_id": CID})
    searches = fake.calls["search"]
    await svc.snapshot_youtube(db, ARTIST, include_videos=False)
    assert fake.calls["search"] == searches      # refresh uses known id + verify, never search


# ---- hourly observation idempotency ------------------------------------------------------------
async def test_hourly_observation_idempotency(db, monkeypatch):
    monkeypatch.setattr(config.settings, "youtube_hourly_observations", True)
    fake = _fake()
    svc = _svc(fake)
    await svc.resolve_youtube(db, ARTIST, query="Arijit Singh", hints={"provider_id": CID})
    t0 = datetime(2026, 8, 9, 10, 15, tzinfo=UTC)
    a = await svc.snapshot_youtube(db, ARTIST, include_videos=False, observed_at=t0)
    b = await svc.snapshot_youtube(db, ARTIST, include_videos=False,
                                   observed_at=t0 + timedelta(minutes=30))  # same hour
    assert a["channel_observations"]["created"] == 3
    assert b["channel_observations"]["created"] == 0            # same hour → one logical observation
    c = await svc.snapshot_youtube(db, ARTIST, include_videos=False,
                                   observed_at=t0 + timedelta(hours=1))     # next hour
    assert c["channel_observations"]["created"] == 3            # new temporal state


# ---- catalogue backfill + registry + batch stats -----------------------------------------------
async def test_catalogue_backfill_bounded_idempotent(db):
    fake = _fake()
    svc = _svc(fake)
    await svc.resolve_youtube(db, ARTIST, query="Arijit Singh", hints={"provider_id": CID})
    first = await svc.backfill_catalogue(db, ARTIST, depth=10)
    assert first["status"] == "OK" and first["videos_registered"] == 1
    second = await svc.backfill_catalogue(db, ARTIST, depth=10)
    assert second["videos_registered"] == 0                     # idempotent on video_id
    v = db.execute(select(YouTubeVideo)).scalar_one()
    assert v.video_id == "v1" and v.canonical_artist_id == ARTIST


async def test_registry_video_snapshot_uses_batch(db):
    fake = _fake()
    svc = _svc(fake)
    await svc.resolve_youtube(db, ARTIST, query="Arijit Singh", hints={"provider_id": CID})
    await svc.backfill_catalogue(db, ARTIST, depth=10)
    out = await svc.snapshot_videos_registry(db, ARTIST)
    assert out["status"] == "OK" and out["path"] == "batch"
    assert fake.calls.get("videos_batch", 0) >= 1
    snap = quota.bucket_snapshot(db, PROVIDER_YOUTUBE)
    assert snap["buckets"]["VIDEO_STATS_BATCH"]["used"] >= 1
    assert db.execute(select(func.count()).select_from(ADO)
                      .where(ADO.scope_type == "CONTENT")).scalar_one() >= 1


async def test_registry_snapshot_fallback_when_empty(db):
    fake = _fake()
    svc = _svc(fake)
    await svc.resolve_youtube(db, ARTIST, query="Arijit Singh", hints={"provider_id": CID})
    out = await svc.snapshot_videos_registry(db, ARTIST)   # no backfill → registry empty
    assert out["path"] == "recent_fallback" and out["status"] == "OK"


# ---- Google Trends official-API readiness (gated) ----------------------------------------------
async def test_trends_official_backfill_gated_access_unavailable(db):
    from artist_intelligence_service.providers.base import ProviderAccessUnavailable
    from artist_intelligence_service.providers.google_trends import GoogleTrendsProvider
    prov = GoogleTrendsProvider()
    assert prov.available is False                 # no alpha creds → ACCESS_UNAVAILABLE, not an error
    assert prov.supports_intraday is False         # no intraday Trends polling loop
    with pytest.raises(ProviderAccessUnavailable):
        await prov.backfill_historical("arijit singh", region="IN", max_days=1825)
    with pytest.raises(ProviderAccessUnavailable):
        await prov.incremental("arijit singh", region="IN")


# ---- identity-queue execution end to end -------------------------------------------------------
async def test_identity_job_resolves_and_enqueues_catalogue(db):
    fake = _fake()
    svc = _svc(fake)
    sched = DemandScheduler(service=svc)
    # discovery-style: only the name is known (no explicit provider_id hint) — search + verify must resolve
    sched.enqueue_identity_discovery(db, ARTIST, display_name="Arijit Singh")
    db.commit()
    out = await sched.run_once(db)
    assert out["outcomes"].get("SUCCEEDED", 0) >= 1
    ident = db.execute(select(ArtistExternalIdentity).where(
        ArtistExternalIdentity.status == RESOLVED,
        ArtistExternalIdentity.provider == PROVIDER_YOUTUBE)).scalars().first()
    assert ident is not None and ident.last_verified_at is not None
    # a resolved identity earns a one-time catalogue backfill job
    assert db.execute(select(func.count()).select_from(DemandRefreshJob)
                      .where(DemandRefreshJob.job_type == JOB_CATALOGUE)).scalar_one() >= 1
