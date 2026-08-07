"""Read models (Phase 5A §10/§16/§17/§24): deltas, insufficient history, freshness, geography joins,
supply-demand join (no score), event-relative timeline (no causal claim), pagination/filtering."""

from datetime import UTC, datetime, timedelta

from artist_intelligence_service import intelligence
from artist_intelligence_service.providers.base import (
    GOOGLE_SEARCH_INTEREST,
    PROVIDER_GOOGLE_TRENDS,
    PROVIDER_YOUTUBE,
    YT_CHANNEL_VIEWS,
    YT_SUBSCRIBERS,
)
from tests.conftest import FakeGraph, days_ago, event_neighbor, seed_obs

ARTIST = "artist:arijit-singh"


def test_7d_delta_and_insufficient_history(db):
    seed_obs(db, artist=ARTIST, provider=PROVIDER_YOUTUBE, metric=YT_SUBSCRIBERS,
             value=100, observed_at=days_ago(8))
    seed_obs(db, artist=ARTIST, provider=PROVIDER_YOUTUBE, metric=YT_SUBSCRIBERS,
             value=130, observed_at=days_ago(0))
    m = intelligence.build_momentum(db, ARTIST)
    d7 = m["components"]["youtube_subscriber_change"]["delta_7d"]
    assert d7["status"] == "OK"
    assert d7["delta"] == 30
    # channel views has only one point → INSUFFICIENT
    seed_obs(db, artist=ARTIST, provider=PROVIDER_YOUTUBE, metric=YT_CHANNEL_VIEWS,
             value=5, observed_at=days_ago(0))
    m2 = intelligence.build_momentum(db, ARTIST)
    assert m2["components"]["youtube_channel_view_velocity"]["delta_7d"]["status"] == "INSUFFICIENT_HISTORY"


def test_momentum_has_no_combined_score(db):
    seed_obs(db, artist=ARTIST, provider=PROVIDER_YOUTUBE, metric=YT_SUBSCRIBERS,
             value=100, observed_at=days_ago(0))
    m = intelligence.build_momentum(db, ARTIST)
    assert "score" not in m
    assert all("score" not in k for k in m["components"])


def test_freshness_stale_flag(db):
    seed_obs(db, artist=ARTIST, provider=PROVIDER_YOUTUBE, metric=YT_SUBSCRIBERS,
             value=100, observed_at=days_ago(3))    # 72h old > 48h threshold
    m = intelligence.build_momentum(db, ARTIST)
    assert m["coverage"]["freshness"]["stale"] is True


def test_pagination_and_filtering(db):
    for i in range(5):
        seed_obs(db, artist=ARTIST, provider=PROVIDER_YOUTUBE, metric=YT_SUBSCRIBERS,
                 value=100 + i, observed_at=days_ago(i))
    from artist_intelligence_service import reads
    page = reads.list_observations(db, ARTIST, provider=PROVIDER_YOUTUBE, limit=2, offset=0)
    assert page["total"] == 5 and len(page["items"]) == 2
    other = reads.list_observations(db, ARTIST, provider=PROVIDER_GOOGLE_TRENDS)
    assert other["total"] == 0


async def test_geography_join(db):
    seed_obs(db, artist=ARTIST, provider=PROVIDER_GOOGLE_TRENDS, metric=GOOGLE_SEARCH_INTEREST,
             value=100, observed_at=days_ago(1), scope_type="REGION", scope_id="IN-WB",
             scope_label="West Bengal", evidence_status="IMPORTED_PROVIDER_EXPORT",
             provenance={"normalization": "trends_0_100_within_pull"})
    future = (datetime.now(UTC) + timedelta(days=20)).isoformat()
    graph = FakeGraph(
        events_for_artist={ARTIST: [event_neighbor("event:x", starts_at=future, city="Kolkata")]},
        event_out={"event:x": [{"relationship": "IN_REGION",
                                "node": {"id": "region:west-bengal", "properties": {}}}]})
    geo = await intelligence.build_geography(db, ARTIST, graph=graph)
    wb = next(r for r in geo["regions"] if r["region_slug"] == "west-bengal")
    assert wb["search_interest"] == 100
    assert wb["observed_supply_count"] == 1
    assert wb["upcoming_live_activity"] == 1
    assert "HIGHER_OBSERVED_DEMAND" in wb["label"]


async def test_supply_demand_join_has_no_combined_score(db):
    seed_obs(db, artist=ARTIST, provider=PROVIDER_YOUTUBE, metric=YT_SUBSCRIBERS,
             value=100, observed_at=days_ago(0))
    future = (datetime.now(UTC) + timedelta(days=10)).isoformat()
    graph = FakeGraph(
        events_for_artist={ARTIST: [event_neighbor("event:x", starts_at=future, city="Mumbai")]},
        event_out={"event:x": [{"relationship": "AT_VENUE",
                                "node": {"id": "venue:antisocial", "properties": {}}}]})
    out = await intelligence.build_demand(db, ARTIST, graph=graph)
    assert out["observed_live_supply"]["event_count"] == 1
    assert out["observed_live_supply"]["upcoming_events"] == 1
    assert "venue:antisocial" in out["observed_live_supply"]["venues"]
    assert "score" not in out


async def test_event_response_timeline_no_causal_claim(db):
    seed_obs(db, artist=ARTIST, provider=PROVIDER_GOOGLE_TRENDS, metric=GOOGLE_SEARCH_INTEREST,
             value=80, observed_at=days_ago(5), scope_type="COUNTRY", scope_id="IN")
    seed_obs(db, artist=ARTIST, provider=PROVIDER_YOUTUBE, metric=YT_CHANNEL_VIEWS,
             value=100, observed_at=days_ago(20))
    seed_obs(db, artist=ARTIST, provider=PROVIDER_YOUTUBE, metric=YT_CHANNEL_VIEWS,
             value=140, observed_at=days_ago(2))
    starts = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    graph = FakeGraph(nodes={"event:x": {"properties": {"starts_at": starts}}})
    out = await intelligence.build_event_response(db, ARTIST, "event:x", graph=graph)
    assert len(out["timeline"]) == 6
    assert "no causal" in out["interpretation"]


async def test_event_response_insufficient_when_no_start_date(db):
    graph = FakeGraph(nodes={"event:y": {"properties": {}}})
    out = await intelligence.build_event_response(db, ARTIST, "event:y", graph=graph)
    assert out["status"] == "INSUFFICIENT_HISTORY"
