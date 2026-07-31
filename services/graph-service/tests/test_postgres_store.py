"""PostgresGraphStore parity tests.

Runs the store against a file-backed SQLite database (portable, no Postgres needed in CI) and
asserts it matches the in-memory store's contract: property-merge upserts, an ``updated_at``
stamp that lives inside properties, idempotent edge MERGE, thin-node endpoints, and neighbour
traversal. The same store class is what runs on Postgres in deployment.
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from graph_service.deps import get_store
from graph_service.main import app
from graph_service.store import Edge, Node, PostgresGraphStore


@pytest.fixture()
def store(tmp_path) -> PostgresGraphStore:
    return PostgresGraphStore(f"sqlite:///{tmp_path}/graph.db")


def test_upsert_node_stamps_updated_at_in_properties(store: PostgresGraphStore) -> None:
    created = store.upsert_node(Node("artist:diljit", "artist", {"display_name": "Diljit"}))
    assert created.type == "artist"
    assert created.properties["display_name"] == "Diljit"
    assert "updated_at" in created.properties  # feed's incremental cursor reads it from here
    assert store.get_node("artist:diljit").properties["updated_at"] == created.properties["updated_at"]


def test_upsert_node_merges_and_advances_cursor(store: PostgresGraphStore) -> None:
    first = store.upsert_node(Node("artist:diljit", "artist", {"display_name": "Diljit", "a": 1}))
    second = store.upsert_node(Node("artist:diljit", "artist", {"b": 2}))
    node = store.get_node("artist:diljit")
    assert node.properties["a"] == 1 and node.properties["b"] == 2  # merged, not replaced
    assert second.properties["updated_at"] >= first.properties["updated_at"]  # cursor moved forward


def test_upsert_node_keeps_type_when_unknown(store: PostgresGraphStore) -> None:
    store.upsert_node(Node("artist:diljit", "artist", {}))
    store.upsert_node(Node("artist:diljit", "unknown", {"x": 1}))  # thin re-touch must not clobber type
    assert store.get_node("artist:diljit").type == "artist"


def test_upsert_edge_is_idempotent_and_creates_thin_endpoints(store: PostgresGraphStore) -> None:
    store.upsert_edge(Edge("event:x", "occurs_at", "venue:y"))
    store.upsert_edge(Edge("event:x", "occurs_at", "venue:y"))  # re-project -> MERGE, not duplicate
    assert store.stats() == {"nodes": 2, "edges": 1}  # both endpoints auto-created as thin nodes
    assert store.get_node("venue:y") is not None


def test_neighbors_directions_and_filter(store: PostgresGraphStore) -> None:
    store.upsert_node(Node("event:x", "event", {"display_name": "Show"}))
    store.upsert_node(Node("venue:y", "venue", {"display_name": "Hall"}))
    store.upsert_node(Node("artist:z", "artist", {"display_name": "Band"}))
    store.upsert_edge(Edge("event:x", "OCCURS_AT", "venue:y"))
    store.upsert_edge(Edge("event:x", "FEATURES", "artist:z"))

    out = store.neighbors("event:x", direction="out")
    assert {(n.relationship, n.node.id) for n in out} == {("OCCURS_AT", "venue:y"), ("FEATURES", "artist:z")}
    only = store.neighbors("event:x", direction="out", relationship="OCCURS_AT")
    assert [n.node.id for n in only] == ["venue:y"]
    incoming = store.neighbors("venue:y", direction="in")
    assert [(n.direction, n.node.id) for n in incoming] == [("in", "event:x")]


def test_list_nodes_filters_by_type(store: PostgresGraphStore) -> None:
    store.upsert_node(Node("event:x", "event", {}))
    store.upsert_node(Node("venue:y", "venue", {}))
    assert {n.id for n in store.list_nodes("event")} == {"event:x"}
    assert {n.id for n in store.list_nodes()} == {"event:x", "venue:y"}


@pytest.fixture()
def feed_client(tmp_path) -> Generator[TestClient, None, None]:
    """The /v1/events feed served off the Postgres store (parity with the in-memory feed test)."""
    store = PostgresGraphStore(f"sqlite:///{tmp_path}/feed.db")
    store.upsert_node(Node("boshow:show:1", "event", {
        "display_name": "Free Folk Nite", "price_min": 599, "verified": True,
        "city": "Kolkata", "source": "boshow", "image_url": "https://img/1.jpg",
    }))
    store.upsert_node(Node("boshow:venue:skinny-mos", "venue", {"display_name": "Skinny Mos"}))
    store.upsert_node(Node("region:west-bengal", "region", {"display_name": "West Bengal"}))
    store.upsert_node(Node("artist:skinny-mos", "artist", {"display_name": "Skinny Mos"}))
    store.upsert_edge(Edge("boshow:show:1", "OCCURS_AT", "boshow:venue:skinny-mos"))
    store.upsert_edge(Edge("boshow:show:1", "IN_REGION", "region:west-bengal"))
    store.upsert_edge(Edge("boshow:show:1", "FEATURES", "artist:skinny-mos"))
    store.upsert_node(Node("district:show:2", "event", {
        "display_name": "Big Concert", "price_min": 1499, "verified": True,
        "city": "Mumbai", "source": "district", "source_url": "https://www.district.in/events/big",
    }))
    app.dependency_overrides[get_store] = lambda: store
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_feed_over_postgres_store_assembles_and_tiers(feed_client: TestClient) -> None:
    events = feed_client.get("/v1/events").json()["events"]
    by_id = {e["id"]: e for e in events}
    assert set(by_id) == {"boshow:show:1", "district:show:2"}
    open_ev = by_id["boshow:show:1"]
    assert open_ev["redistribution_tier"] == "open"
    assert open_ev["venue_id"] == "boshow:venue:skinny-mos"
    assert open_ev["region_id"] == "region:west-bengal"
    assert open_ev["artist_ids"] == ["artist:skinny-mos"]
    assert open_ev["updated_at"]  # carried from node properties -> usable as sync cursor
    assert by_id["district:show:2"]["redistribution_tier"] == "link_only"
