from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from graph_service.deps import get_store
from graph_service.main import app
from graph_service.store import InMemoryGraphStore, normalize_rel


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    store = InMemoryGraphStore()
    app.dependency_overrides[get_store] = lambda: store
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_normalize_rel() -> None:
    assert normalize_rel("signed_to") == "SIGNED_TO"
    assert normalize_rel("occurs at") == "OCCURS_AT"
    assert normalize_rel("features") == "FEATURES"


def test_upsert_and_get_node(client: TestClient) -> None:
    resp = client.post(
        "/v1/graph/nodes",
        json={"id": "label:t-series", "type": "label", "properties": {"mbid": "d8067fa7"}},
    )
    assert resp.status_code == 200
    assert resp.json()["type"] == "label"

    got = client.get("/v1/graph/nodes/label:t-series")
    assert got.status_code == 200
    assert got.json()["properties"]["mbid"] == "d8067fa7"


def test_get_missing_node_404(client: TestClient) -> None:
    assert client.get("/v1/graph/nodes/artist:nobody").status_code == 404


def test_upsert_node_is_idempotent_and_merges_props(client: TestClient) -> None:
    client.post("/v1/graph/nodes", json={"id": "artist:arijit-singh", "type": "artist"})
    client.post(
        "/v1/graph/nodes",
        json={"id": "artist:arijit-singh", "type": "artist", "properties": {"kg_mid": "/m/08hr72"}},
    )
    assert client.get("/v1/graph/stats").json()["nodes"] == 1
    node = client.get("/v1/graph/nodes/artist:arijit-singh").json()
    assert node["properties"]["kg_mid"] == "/m/08hr72"


def test_edge_creates_endpoints_and_normalizes_rel(client: TestClient) -> None:
    resp = client.post(
        "/v1/graph/edges",
        json={"source": "artist:arijit-singh", "relationship": "signed_to", "target": "label:t-series"},
    )
    assert resp.status_code == 200
    assert resp.json()["relationship"] == "SIGNED_TO"
    # both endpoints auto-created as thin nodes
    assert client.get("/v1/graph/stats").json() == {"nodes": 2, "edges": 1}


def test_directional_neighbors(client: TestClient) -> None:
    client.post(
        "/v1/graph/edges",
        json={"source": "artist:arijit-singh", "relationship": "signed_to", "target": "label:t-series"},
    )
    # outgoing from the artist -> the label
    out = client.get("/v1/graph/nodes/artist:arijit-singh/neighbors?direction=out").json()
    assert out["count"] == 1
    assert out["neighbors"][0]["node"]["id"] == "label:t-series"
    assert out["neighbors"][0]["direction"] == "out"
    # incoming to the label -> the artist
    inc = client.get("/v1/graph/nodes/label:t-series/neighbors?direction=in").json()
    assert inc["count"] == 1
    assert inc["neighbors"][0]["node"]["id"] == "artist:arijit-singh"


def test_neighbors_relationship_filter(client: TestClient) -> None:
    client.post(
        "/v1/graph/edges",
        json={"source": "artist:x", "relationship": "signed_to", "target": "label:y"},
    )
    client.post(
        "/v1/graph/edges",
        json={"source": "artist:x", "relationship": "strong_in", "target": "city:mumbai"},
    )
    filtered = client.get(
        "/v1/graph/nodes/artist:x/neighbors?direction=out&relationship=strong_in"
    ).json()
    assert filtered["count"] == 1
    assert filtered["neighbors"][0]["node"]["id"] == "city:mumbai"


def test_edge_upsert_is_idempotent(client: TestClient) -> None:
    body = {"source": "artist:x", "relationship": "signed_to", "target": "label:y"}
    client.post("/v1/graph/edges", json=body)
    client.post("/v1/graph/edges", json=body)
    assert client.get("/v1/graph/stats").json()["edges"] == 1


def test_health_reports_backend(client: TestClient) -> None:
    # default backend from settings (neo4j); the store is overridden to in-memory for tests
    assert client.get("/health").json()["service"] == "graph-service"
