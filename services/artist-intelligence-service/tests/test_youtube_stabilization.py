"""Phase 5B.2.8 — YouTube acquisition stabilization.

Covers: call-based Search-Queries gating (robust to stale pre-fix 100-unit rows), the high-priority
search reserve, catalogue-discovery recurrence, snapshot semantics (metric observations ≠ temporal
snapshots), and stuck-state diagnostics.
"""

from datetime import UTC, datetime, timedelta

import pytest

from artist_intelligence_service import config, quota
from artist_intelligence_service.models import ProviderQuotaBucketDay as PQB
from artist_intelligence_service.models import ArtistExternalIdentity as AEI
from artist_intelligence_service.pipeline import youtube_pipeline
from artist_intelligence_service.providers.base import PROVIDER_YOUTUBE
from artist_intelligence_service import videos as vids
from tests.conftest import seed_obs

Y = PROVIDER_YOUTUBE


def _bucket(db, bucket, *, units, requests, on=None):
    day = quota.quota_date_for(Y, on=on)
    now = datetime.now(UTC)
    db.add(PQB(id=f"{bucket}-{day}", provider=Y, quota_date=day, bucket=bucket,
               units=units, requests=requests, successful_calls=requests, failed_calls=0,
               quota_errors=0, created_at=now, updated_at=now))
    db.flush()


# ---- §2/§23.1/§23.2: call-based gating, robust to stale units --------------------------------
def test_search_gated_by_calls_not_stale_units(db):
    # a day that accrued 35 stale 100-unit search rows (3500 units) but only 35 real calls
    _bucket(db, quota.BUCKET_SEARCH, units=3500, requests=35)
    assert quota.search_calls_used(db, Y) == 35              # accounted by CALL count, not units
    assert quota.can_spend(db, Y, quota.BUCKET_SEARCH, 1) is True   # 35 < 100 → NOT blocked by stale units
    snap = quota.bucket_snapshot(db, Y)
    assert snap["search_queries"]["used_calls"] == 35 and snap["search_queries"]["remaining_calls"] == 65


def test_fresh_quota_day_has_empty_search_bucket(db):
    # a different (fresh) quota day is a separate row: no stale units carry across the rollover
    yesterday = quota.quota_date_for(Y) - timedelta(days=1)
    _bucket(db, quota.BUCKET_SEARCH, units=3500, requests=35, on=yesterday)
    assert quota.search_calls_used(db, Y, on=yesterday) == 35
    assert quota.search_calls_used(db, Y) == 0               # today is independent
    assert quota.can_spend(db, Y, quota.BUCKET_SEARCH, 1) is True


def test_search_cap_blocks_at_daily_calls(db):
    _bucket(db, quota.BUCKET_SEARCH, units=100, requests=100)   # exactly the 100-call quota spent
    assert quota.can_spend(db, Y, quota.BUCKET_SEARCH, 1) is False


# ---- §4: high-priority search reserve --------------------------------------------------------
def test_reserve_protects_high_priority(db, monkeypatch):
    monkeypatch.setattr(config.settings, "youtube_search_allocation_enforced", True)
    monkeypatch.setattr(config.settings, "youtube_search_alloc_reserve", 0.05)   # 5 of 100 reserved
    _bucket(db, quota.BUCKET_SEARCH, units=96, requests=96)      # into the reserve band (96..100)
    # ordinary backlog is refused (keep the reserve); a high-priority job may use it
    assert quota.can_spend_search(db, Y, quota.SEARCH_UNRESOLVED, 1,
                                  others_have_backlog=True, high_priority=False) is False
    assert quota.can_spend_search(db, Y, quota.SEARCH_UNRESOLVED, 1,
                                  others_have_backlog=True, high_priority=True) is True


# ---- §12/§18/§20: pipeline funnel + snapshot semantics ---------------------------------------
def _identity(db, artist, status):
    now = datetime.now(UTC)
    db.add(AEI(id="pending:" + artist, canonical_artist_id=artist, provider=Y,
               identity_type="CHANNEL_ID", provider_id="pending:" + artist, status=status,
               confidence=0.0, first_seen_at=now, created_at=now, updated_at=now,
               identity_metadata={}, provenance={}))
    db.flush()


def test_pipeline_snapshot_semantics_metric_vs_temporal(db):
    _identity(db, "artist:a", "RESOLVED")
    vids.upsert_video(db, video_id="v1", channel_id="UCa", canonical_artist_id="artist:a")
    t1 = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    # ONE snapshot, THREE metric observations (views/likes/comments) at the same collection time
    for metric in ("YOUTUBE_VIDEO_VIEWS", "YOUTUBE_VIDEO_LIKES", "YOUTUBE_VIDEO_COMMENTS"):
        seed_obs(db, artist="artist:a", provider=Y, metric=metric, value=100, observed_at=t1,
                 scope_type="CONTENT", scope_id="v1", dedup_extra="v1", bucket="2026-08-14T09")
    p = youtube_pipeline(db)
    assert p["snapshot_semantics"]["metric_observations"] == 3        # 3 metric rows
    assert p["snapshot_semantics"]["videos_with_1_snapshot"] == 1     # ...but only 1 temporal snapshot
    assert p["snapshot_semantics"]["videos_with_2plus_snapshots"] == 0
    assert p["owned_content"]["videos_ge2_snapshots"] == 0


def test_pipeline_two_snapshots_are_velocity_ready(db):
    _identity(db, "artist:a", "RESOLVED")
    vids.upsert_video(db, video_id="v1", channel_id="UCa", canonical_artist_id="artist:a")
    for hh, val in ((9, 100), (10, 160)):   # two DISTINCT hourly snapshots
        seed_obs(db, artist="artist:a", provider=Y, metric="YOUTUBE_VIDEO_VIEWS", value=val,
                 observed_at=datetime(2026, 8, 14, hh, 0, tzinfo=UTC),
                 scope_type="CONTENT", scope_id="v1", dedup_extra="v1", bucket=f"2026-08-14T{hh:02d}")
    p = youtube_pipeline(db)
    assert p["snapshot_semantics"]["videos_with_2plus_snapshots"] == 1   # velocity possible
    # only 2 snapshots < movement_min_observations (3) → classification stays INSUFFICIENT_HISTORY
    assert p["owned_content"]["videos_with_sufficient_movement_history"] == 0


# ---- §22: stuck-state diagnostics -------------------------------------------------------------
def test_stuck_state_resolved_without_catalogue(db):
    _identity(db, "artist:stuck", "RESOLVED")   # RESOLVED but no catalogue job and no videos
    p = youtube_pipeline(db)
    assert p["stuck_states"]["resolved_without_catalogue_job"]["count"] == 1
    assert "artist:stuck" in p["stuck_states"]["resolved_without_catalogue_job"]["sample"]
    assert p["stuck_states"]["any_stuck"] is True


def test_stuck_state_clean_when_healthy(db):
    _identity(db, "artist:ok", "RESOLVED")
    vids.upsert_video(db, video_id="v1", channel_id="UCok", canonical_artist_id="artist:ok")
    # give it a catalogue + video job + an observation so no stuck detector fires
    now = datetime.now(UTC)
    from artist_intelligence_service.models import DemandRefreshJob as JOB
    for jt in ("YOUTUBE_CATALOGUE_BACKFILL", "YOUTUBE_VIDEO_SNAPSHOT"):
        db.add(JOB(id=jt, dedup_key=jt, canonical_artist_id="artist:ok", provider=Y, job_type=jt,
                   status="SUCCEEDED", scheduled_at=now, created_at=now, updated_at=now, priority=40))
    seed_obs(db, artist="artist:ok", provider=Y, metric="YOUTUBE_VIDEO_VIEWS", value=1,
             observed_at=now, scope_type="CONTENT", scope_id="v1", dedup_extra="v1")
    db.flush()
    p = youtube_pipeline(db)
    assert p["stuck_states"]["any_stuck"] is False


# ---- §17/§23.16: video statistics are batch-bounded ------------------------------------------
@pytest.mark.asyncio
async def test_video_stats_are_batch_bounded(db, monkeypatch):
    from artist_intelligence_service.service import DemandService
    from artist_intelligence_service.providers.youtube import YouTubeProvider
    from tests.conftest import FakeSignal, candidate
    monkeypatch.setattr(config.settings, "youtube_catalogue_backfill_depth", 200)
    A = "artist:batch"
    vlist = [{"video_id": f"v{i}", "title": f"t{i}", "published_at": "2026-07-01T00:00:00Z",
              "views": 10 + i, "likes": i, "comments": 0} for i in range(60)]
    fake = FakeSignal(search={"batch artist": [candidate("UCbatch", "Batch Artist", topic=True)]},
                      channel={"UCbatch": {"title": "Batch Artist", "subscriber_count": 1}},
                      videos={"UCbatch": vlist})
    svc = DemandService(youtube=YouTubeProvider(signal=fake))
    await svc.resolve_youtube(db, A, query="Batch Artist"); db.commit()
    for v in vlist:   # register 60 owned videos
        vids.upsert_video(db, video_id=v["video_id"], channel_id="UCbatch", canonical_artist_id=A)
    db.commit()
    fake.calls["videos_batch"] = 0
    await svc.snapshot_videos_registry(db, A); db.commit()
    # 60 videos → ceil(60/50) = 2 batch requests (videos.list id limit is 50), never 60 per-video reads
    assert fake.calls["videos_batch"] == 2
    assert quota.bucket_snapshot(db, Y)["buckets"]["VIDEO_STATS_BATCH"]["requests"] == 2
