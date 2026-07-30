import pytest
from fastapi.testclient import TestClient

from analytics_service.deps import get_graph_client
from analytics_service.main import app
from analytics_service.scoring import (
    artist_demand,
    momentum_score,
    popularity_score,
    reach_score,
    region_intelligence,
)


# ---- pure scoring (deterministic, no network) ----
def test_momentum_score_maps_direction() -> None:
    assert momentum_score("rising") == 1.0
    assert momentum_score("steady") == 0.6
    assert momentum_score("falling") == 0.2
    assert momentum_score(None) == 0.5
    assert momentum_score("rising", breakout=True) == 1.0  # capped


def test_popularity_score_log_scaled_or_none() -> None:
    assert popularity_score(None) is None
    assert popularity_score(0) is None
    assert popularity_score(1_000_000) == round(6 / 9, 3)
    assert popularity_score(10**12) == 1.0  # capped


def test_reach_score_saturates() -> None:
    assert reach_score(0) == 0.0
    assert reach_score(5) == 1.0
    assert reach_score(10) == 1.0


def test_artist_demand_renormalizes_over_present_signals() -> None:
    # Only momentum + reach present (typical Trends artist, no YouTube subs).
    d = artist_demand("artist:diljit-dosanjh", {"search_momentum": "steady"}, ["Punjab", "Delhi", "Haryana", "Chandigarh", "Himachal Pradesh"])
    # (0.4*0.6 + 0.3*1.0) / (0.4+0.3) * 100 = 77.1
    assert d.demand_score == 77.1
    assert "popularity" in d.missing_signals
    assert d.strongest_markets == ["Punjab", "Delhi", "Haryana"]


def test_artist_demand_uses_popularity_when_present() -> None:
    d = artist_demand("artist:x", {"search_momentum": "rising", "subscriber_count": 1_000_000}, ["A"])
    assert set(d.components) == {"momentum", "popularity", "reach"}
    assert d.missing_signals == []


def test_region_intelligence_demand_vs_supply() -> None:
    events = [
        {"id": "event:free-folk-nite", "properties": {"fill_ratio": 0.2, "price_min": 599}},
        {"id": "event:jamsteady", "properties": {"fill_ratio": 0.05, "price_min": 499}},
    ]
    r = region_intelligence("region:west-bengal", ["Arijit Singh", "Shreya Ghoshal"], events)
    assert r.demand_signals == 2 and r.supply_signals == 2
    assert r.avg_fill_ratio == round((0.2 + 0.05) / 2, 3)
    assert r.avg_price == 549.0
    assert r.demand_supply_ratio == 1.0
    assert r.verdict == "balanced"


def test_region_undersupplied_when_demand_no_supply() -> None:
    r = region_intelligence("region:goa", ["Some Artist"], [])
    assert r.verdict == "undersupplied"
    assert r.avg_fill_ratio is None


# ---- routes (stubbed graph client) ----
class _StubGraph:
    def __init__(self, nodes, neighbors) -> None:
        self._nodes, self._neighbors = nodes, neighbors

    async def get_node(self, node_id):
        return self._nodes.get(node_id)

    async def neighbors(self, node_id, *, direction="both", relationship=None):
        out = self._neighbors.get(node_id, [])
        if relationship:
            out = [n for n in out if n["relationship"].lower() == relationship.lower()]
        if direction != "both":
            out = [n for n in out if n["direction"] == direction]
        return out


@pytest.fixture()
def client_with(monkeypatch):
    def _make(nodes, neighbors) -> TestClient:
        stub = _StubGraph(nodes, neighbors)
        app.dependency_overrides[get_graph_client] = lambda: stub
        return TestClient(app)

    yield _make
    app.dependency_overrides.clear()


def test_artist_route_scores_from_graph(client_with) -> None:
    nodes = {"artist:diljit-dosanjh": {"id": "artist:diljit-dosanjh", "type": "artist", "properties": {"search_momentum": "steady"}}}
    neighbors = {"artist:diljit-dosanjh": [
        {"relationship": "STRONG_IN", "direction": "out", "node": {"id": "region:punjab", "type": "region", "properties": {"display_name": "Punjab"}}},
    ]}
    resp = client_with(nodes, neighbors).get("/v1/analytics/artists/artist:diljit-dosanjh")
    assert resp.status_code == 200
    body = resp.json()
    assert body["demand_score"] > 0
    assert body["strongest_markets"] == ["Punjab"]


def test_artist_route_404_when_absent(client_with) -> None:
    assert client_with({}, {}).get("/v1/analytics/artists/artist:nobody").status_code == 404


def test_region_route_combines_demand_and_supply(client_with) -> None:
    nodes = {"region:west-bengal": {"id": "region:west-bengal", "type": "region", "properties": {}}}
    neighbors = {"region:west-bengal": [
        {"relationship": "STRONG_IN", "direction": "in", "node": {"id": "artist:arijit-singh", "type": "artist", "properties": {"display_name": "Arijit Singh"}}},
        {"relationship": "IN_REGION", "direction": "in", "node": {"id": "event:free-folk-nite", "type": "event", "properties": {"fill_ratio": 0.2}}},
    ]}
    resp = client_with(nodes, neighbors).get("/v1/analytics/regions/region:west-bengal")
    assert resp.status_code == 200
    body = resp.json()
    assert body["demand_signals"] == 1 and body["supply_signals"] == 1
    assert body["demanding_artists"] == ["Arijit Singh"]
    assert body["events"] == ["event:free-folk-nite"]
