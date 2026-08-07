"""YouTube provider (Phase 5A §6-§9/§24): snapshots, rounded subscribers, videos, known-id refresh,
quota accounting + exhaustion, provider errors, duplicate-observation prevention."""

import pytest
from sqlalchemy import select

from artist_intelligence_service import config
from artist_intelligence_service.models import ArtistDemandObservation as ADO
from artist_intelligence_service.models import ProviderQuotaDay
from artist_intelligence_service.providers.youtube import YouTubeProvider
from artist_intelligence_service.service import DemandService, QuotaExhausted
from tests.conftest import FakeSignal, candidate

ARTIST = "artist:arijit-singh"
CID = "UC_real"


def _fake():
    return FakeSignal(
        search={"arijit singh": [candidate("UC_real", "Arijit Singh", topic=True)]},
        channel={CID: {"subscriber_count": 31200000, "total_view_count": 9800000000,
                       "video_count": 180}},
        videos={CID: [{"video_id": "v1", "title": "Live", "published_at": "2026-07-20T12:00:00Z",
                       "views": 4200000, "likes": 310000, "comments": 12000}]})


async def _resolved_service(db, fake):
    svc = DemandService(youtube=YouTubeProvider(signal=fake))
    await svc.resolve_youtube(db, ARTIST, query="Arijit Singh", hints={"provider_id": CID})
    return svc


async def test_channel_snapshot_and_rounded_subscribers(db):
    fake = _fake()
    svc = await _resolved_service(db, fake)
    await svc.snapshot_youtube(db, ARTIST, include_videos=False)
    rows = {r.metric: r for r in db.execute(select(ADO)).scalars()}
    assert rows["YOUTUBE_CHANNEL_VIEWS"].evidence_status == "DIRECT_PROVIDER_VALUE"
    assert rows["YOUTUBE_VIDEO_COUNT"].evidence_status == "DIRECT_PROVIDER_VALUE"
    subs = rows["YOUTUBE_SUBSCRIBERS"]
    assert subs.evidence_status == "PROVIDER_REPORTED"          # rounded, not exact
    assert subs.provenance.get("precision") == "rounded_3sf"


async def test_video_snapshot_persists_per_video_content_metrics(db):
    fake = _fake()
    svc = await _resolved_service(db, fake)
    await svc.snapshot_youtube(db, ARTIST, include_channel=False, include_videos=True)
    content = db.execute(select(ADO).where(ADO.scope_type == "CONTENT")).scalars().all()
    metrics = {r.metric for r in content}
    assert metrics == {"YOUTUBE_VIDEO_VIEWS", "YOUTUBE_VIDEO_LIKES", "YOUTUBE_VIDEO_COMMENTS"}
    assert all(r.scope_id == "v1" for r in content)


async def test_refresh_uses_known_id_not_search(db):
    fake = _fake()
    svc = await _resolved_service(db, fake)
    searches_before = fake.calls["search"]
    await svc.snapshot_youtube(db, ARTIST)
    assert fake.calls["search"] == searches_before        # no search during refresh
    assert fake.calls["channel"] >= 1


async def test_duplicate_observation_prevention(db):
    fake = _fake()
    svc = await _resolved_service(db, fake)
    first = await svc.snapshot_youtube(db, ARTIST, include_videos=False)
    second = await svc.snapshot_youtube(db, ARTIST, include_videos=False)
    assert first["channel_observations"]["created"] == 3
    assert second["channel_observations"]["created"] == 0   # same day → idempotent
    assert second["channel_observations"]["duplicates"] == 3


async def test_quota_accounting(db):
    fake = _fake()
    svc = await _resolved_service(db, fake)   # one search
    await svc.snapshot_youtube(db, ARTIST)     # channel read(1) + video read(3)
    row = db.execute(select(ProviderQuotaDay)).scalar_one()
    assert row.search_requests == 1
    assert row.search_quota_units == 100
    assert row.non_search_quota_units >= 4


async def test_quota_exhaustion(db, monkeypatch):
    monkeypatch.setattr(config.settings, "youtube_max_searches_per_day", 1)
    fake = FakeSignal(search={"arijit singh": [candidate("UC_real", "Arijit Singh", topic=True)]})
    svc = DemandService(youtube=YouTubeProvider(signal=fake))
    await svc.resolve_youtube(db, ARTIST, query="Arijit Singh")   # spends the 1 search
    with pytest.raises(QuotaExhausted):
        await svc.resolve_youtube(db, ARTIST, query="Arijit Singh")


async def test_provider_error_propagates(db):
    fake = _fake()
    fake.fail_channel = True
    svc = await _resolved_service(db, fake)
    with pytest.raises(RuntimeError):
        await svc.snapshot_youtube(db, ARTIST)
