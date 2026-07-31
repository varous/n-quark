from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from graph_service.deps import get_store
from graph_service.main import app
from graph_service.redistribution import redistribution_tier
from graph_service.store import Edge, InMemoryGraphStore, Node


def test_redistribution_tier_policy() -> None:
    assert redistribution_tier("boshow", 300, True) == "open"        # grassroots paid -> open
    assert redistribution_tier("luma", 0, True) == "open"            # free community
    assert redistribution_tier("knowafest", None, True) == "open"    # unknown-price grassroots
    assert redistribution_tier("district", 1499, True) == "link_only"   # mainstream paid
    assert redistribution_tier("skillbox", 1999, True) == "link_only"
    assert redistribution_tier("district", 0, True) == "open"        # mainstream but free -> open
    assert redistribution_tier("allevents", 0, True) == "link_only"  # aggregator -> link out
    assert redistribution_tier("townscript", 0, False) == "excluded"  # unverified / spam


@pytest.fixture()
def seeded() -> Generator[TestClient, None, None]:
    store = InMemoryGraphStore()
    # open: grassroots-paid event with venue + region + artist
    store.upsert_node(Node("boshow:show:1", "event", {
        "display_name": "Free Folk Nite", "price_min": 599, "verified": True,
        "city": "Kolkata", "image_url": "https://img/1.jpg", "fill_ratio": 0.2,
    }))
    store.upsert_node(Node("boshow:venue:skinny-mos", "venue", {"display_name": "Skinny Mos"}))
    store.upsert_node(Node("region:west-bengal", "region", {"display_name": "West Bengal"}))
    store.upsert_node(Node("artist:skinny-mos", "artist", {"display_name": "Skinny Mos"}))
    store.upsert_edge(Edge("boshow:show:1", "OCCURS_AT", "boshow:venue:skinny-mos"))
    store.upsert_edge(Edge("boshow:show:1", "IN_REGION", "region:west-bengal"))
    store.upsert_edge(Edge("boshow:show:1", "FEATURES", "artist:skinny-mos"))
    # link_only: mainstream-paid event, carries a source_url to link out to
    store.upsert_node(Node("district:show:2", "event", {
        "display_name": "Big Concert", "price_min": 1499, "verified": True,
        "city": "Mumbai", "source_url": "https://www.district.in/events/big",
    }))
    # excluded: unverified/spam
    store.upsert_node(Node("townscript:show:3", "event", {
        "display_name": "Spam Training", "price_min": 0, "verified": False, "city": "X",
    }))
    app.dependency_overrides[get_store] = lambda: store
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_feed_excludes_spam_by_default(seeded: TestClient) -> None:
    body = seeded.get("/v1/events").json()
    ids = {e["id"] for e in body["events"]}
    assert ids == {"boshow:show:1", "district:show:2"}  # townscript spam withheld
    assert body["count"] == 2


def test_feed_assembles_relationships_and_tier(seeded: TestClient) -> None:
    ev = next(e for e in seeded.get("/v1/events").json()["events"] if e["id"] == "boshow:show:1")
    assert ev["redistribution_tier"] == "open"
    assert ev["venue"] == "Skinny Mos" and ev["venue_id"] == "boshow:venue:skinny-mos"
    assert ev["region"] == "West Bengal" and ev["region_id"] == "region:west-bengal"
    assert ev["artists"] == ["Skinny Mos"] and ev["artist_ids"] == ["artist:skinny-mos"]
    assert ev["is_free"] is False and ev["image_url"] == "https://img/1.jpg"


def test_feed_link_only_carries_source_url(seeded: TestClient) -> None:
    ev = next(e for e in seeded.get("/v1/events?tier=link_only").json()["events"])
    assert ev["id"] == "district:show:2"
    assert ev["source_url"] == "https://www.district.in/events/big"


def test_feed_filters(seeded: TestClient) -> None:
    assert {e["id"] for e in seeded.get("/v1/events?source=boshow").json()["events"]} == {"boshow:show:1"}
    assert seeded.get("/v1/events?city=mumbai").json()["count"] == 1
    # explicit tier=excluded surfaces the withheld one (for review tooling)
    assert {e["id"] for e in seeded.get("/v1/events?tier=excluded").json()["events"]} == {"townscript:show:3"}
