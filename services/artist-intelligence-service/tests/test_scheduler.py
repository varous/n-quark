"""Refresh scheduler (Phase 5A §15/§24): enqueue idempotency, lease/claim, execute+reschedule,
restart-safety, retry classification, failure isolation."""


from sqlalchemy import func, select

from artist_intelligence_service.models import ArtistDemandObservation as ADO
from artist_intelligence_service.models import DemandRefreshJob
from artist_intelligence_service.providers.youtube import YouTubeProvider
from artist_intelligence_service.scheduler import DemandScheduler
from artist_intelligence_service.service import DemandService
from tests.conftest import FakeSignal, candidate

ARTIST = "artist:arijit-singh"
CID = "UC_real"


def _fake():
    return FakeSignal(
        search={"arijit singh": [candidate("UC_real", "Arijit Singh", topic=True)]},
        channel={CID: {"subscriber_count": 31200000, "total_view_count": 9800000000,
                       "video_count": 180}},
        videos={CID: [{"video_id": "v1", "views": 4200000, "likes": 310000, "comments": 12000,
                       "published_at": "2026-07-20T12:00:00Z"}]})


async def _resolve(db, fake):
    svc = DemandService(youtube=YouTubeProvider(signal=fake))
    await svc.resolve_youtube(db, ARTIST, query="Arijit Singh", hints={"provider_id": CID})
    db.commit()
    return svc


async def test_enqueue_is_idempotent(db):
    svc = await _resolve(db, _fake())
    sched = DemandScheduler(service=svc)
    a = sched.enqueue_due(db)
    b = sched.enqueue_due(db)
    assert a["jobs_created"] == 3          # channel + video + catalogue (5B.2.8 §10)
    assert b["jobs_created"] == 0          # same window → no duplicates
    db.commit()


async def test_run_once_executes_and_reschedules(db):
    svc = await _resolve(db, _fake())
    sched = DemandScheduler(service=svc)
    out = await sched.run_once(db)
    assert out["claimed"] == 3
    assert out["outcomes"].get("SUCCEEDED") == 3
    # observations landed + jobs marked SUCCEEDED with a next_refresh_at
    assert db.execute(select(func.count()).select_from(ADO)).scalar_one() > 0
    jobs = db.execute(select(DemandRefreshJob)).scalars().all()
    assert all(j.status == "SUCCEEDED" and j.next_refresh_at is not None for j in jobs)


async def test_restart_safe(db):
    """State lives in Postgres: a fresh scheduler instance sees the completed jobs and re-drains
    idempotently (no duplicate observations)."""
    svc = await _resolve(db, _fake())
    await DemandScheduler(service=svc).run_once(db)
    obs_after_first = db.execute(select(func.count()).select_from(ADO)).scalar_one()
    # simulate a process restart: brand-new scheduler + service, same DB
    svc2 = DemandService(youtube=YouTubeProvider(signal=_fake()))
    await DemandScheduler(service=svc2).run_once(db)
    obs_after_restart = db.execute(select(func.count()).select_from(ADO)).scalar_one()
    assert obs_after_restart == obs_after_first     # same day → no duplication


async def test_failure_is_isolated_and_retried(db):
    fake = _fake()
    fake.fail_videos = True                  # video snapshots fail; channel snapshots succeed
    svc = await _resolve(db, fake)
    sched = DemandScheduler(service=svc)
    out = await sched.run_once(db)
    # the video job failed-retryable; the channel job still succeeded (isolation)
    assert out["outcomes"].get("FAILED_RETRYABLE", 0) >= 1
    assert out["outcomes"].get("SUCCEEDED", 0) >= 1
    vid_job = db.execute(select(DemandRefreshJob)
                         .where(DemandRefreshJob.job_type == "YOUTUBE_VIDEO_SNAPSHOT")).scalar_one()
    assert vid_job.status == "FAILED_RETRYABLE"
    assert vid_job.consecutive_failures == 1
