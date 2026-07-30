from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from signal_service.adapters.ticketing import (
    MockTicketingProvider,
    event_from_boshow,
    normalize_event,
    split_lineup,
    split_location,
)
from signal_service.config import settings
from signal_service.graph_projection import project_ticketing_graph
from signal_service.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_split_location_city_state_country() -> None:
    assert split_location("Kolkata-West Bengal-India") == ("Kolkata", "West Bengal", "India")
    assert split_location("Pune-Maharashtra-India") == ("Pune", "Maharashtra", "India")


def test_split_lineup_drops_generic_prefix_and_dedupes() -> None:
    assert split_lineup("Skinny Mos • Dolinman • Gaboo") == ["Skinny Mos", "Dolinman", "Gaboo"]
    # "Live at X" is a placeholder, not a performer — fall back to member_name
    assert split_lineup("Live at Skinny Mos", member_name="Skinny Mos") == ["Skinny Mos"]


def test_fill_ratio_is_the_demand_ground_truth() -> None:
    event = event_from_boshow(
        {
            "display_name": "ATSP", "show_type": "Performance Art",
            "name_of_artist": "At the Still Point", "location": "The Urban Theatre Project",
            "city": "Kolkata-West Bengal-India", "default_price": 600, "currency": "INR",
            "real_show_date": "2026-07-31T19:30:00.000Z", "gc": 49, "tickets_sold": 31,
            "show_id": ["256f6c6e"], "slug": "atsp",
        }
    )
    assert event.fill_ratio == round(31 / 49, 3)
    assert event.region == "West Bengal"
    assert event.price_min == 600.0


def test_fill_ratio_none_without_capacity() -> None:
    event = event_from_boshow({"display_name": "x", "city": "Kolkata-West Bengal-India", "slug": "x"})
    assert event.fill_ratio is None


async def test_normalize_emits_demand_and_relationship_observations() -> None:
    event = await MockTicketingProvider().extract("free-folk-nite-01082026")
    obs = normalize_event(event)
    attrs = {o.attribute for o in obs}
    assert {"fill_ratio", "occurs_at_venue", "lineup", "in_region", "source_event_id"} <= attrs
    # everything keys on the type-neutral event handle
    assert all(o.entity == "boshow:show:a7ed0638-ef5e-4f98-801b-ad46e3a75a6d" for o in obs)
    fill = next(o for o in obs if o.attribute == "fill_ratio")
    assert fill.value == round(10 / 50, 3)
    assert fill.evidence["tickets_sold"] == 10 and fill.evidence["capacity"] == 50


async def test_normalize_provenance_is_compliant_public_scrape() -> None:
    event = await MockTicketingProvider().extract("jamsteady-with-cherry-mrong-31072026")
    prov = normalize_event(event)[0].metadata["provenance"]
    assert prov["acquisition_method"] == "public_scrape"
    assert prov["logged_out"] is True and prov["robots_respected"] is True
    assert prov["contains_pii"] is False


def test_project_ticketing_graph_builds_structural_edges() -> None:
    projection = project_ticketing_graph(
        event_id="event:free-folk-nite",
        event_properties={"fill_ratio": 0.2, "category": "Music"},
        venue_id="venue:skinny-mos",
        venue_name="Skinny Mos",
        artists=[("artist:skinny-mos", "Skinny Mos"), ("artist:dolinman", "Dolinman")],
        region="West Bengal",
    )
    rels = {(e.source, e.relationship, e.target) for e in projection.edges}
    assert ("event:free-folk-nite", "OCCURS_AT", "venue:skinny-mos") in rels
    assert ("event:free-folk-nite", "FEATURES", "artist:skinny-mos") in rels
    assert ("event:free-folk-nite", "FEATURES", "artist:dolinman") in rels
    assert ("event:free-folk-nite", "IN_REGION", "region:west-bengal") in rels
    assert {n.type for n in projection.nodes} == {"event", "venue", "artist", "region"}


@pytest.fixture()
def _offline_ticketing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "ticketing_provider", "mock")

    async def fake_resolve(self, **kwargs):
        etype = kwargs["entity_type"]
        slug = kwargs["display_name"].lower().replace(" ", "-")
        return {"canonical_id": f"{etype}:{slug}", "created": True}

    async def fake_projection(self, projection):
        return {"nodes": len(projection.nodes), "edges": len(projection.edges)}

    monkeypatch.setattr(
        "signal_service.routes.ticketing.EntityServiceClient.resolve", fake_resolve
    )
    monkeypatch.setattr(
        "signal_service.routes.ticketing.GraphServiceClient.upsert_projection", fake_projection
    )


def test_discover_lists_event_refs(client: TestClient, _offline_ticketing: None) -> None:
    resp = client.get("/v1/signals/ticketing/discover")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "mock"
    assert "free-folk-nite-01082026" in body["event_refs"]


def test_ingest_with_trace_is_multi_entity(client: TestClient, _offline_ticketing: None) -> None:
    stored = [{"id": f"00000000-0000-0000-0000-00000000000{i}"} for i in range(9)]
    with patch(
        "signal_service.routes.ticketing.ObservationServiceClient.append_observations",
        new_callable=AsyncMock,
        return_value=stored,
    ):
        resp = client.post(
            "/v1/signals/ticketing/events/free-folk-nite-01082026/ingest?trace=true"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fill_ratio"] == round(10 / 50, 3)
    assert [r["stage"] for r in body["trace"]] == ["ingestion", "observation", "entity", "graph"]
    # entity stage resolved an event, a venue, and multiple artists
    assert body["resolved"]["event"] == "event:free-folk-nite"
    assert body["resolved"]["venue"] == "venue:skinny-mos"
    assert len(body["resolved"]["artists"]) >= 2
    # graph stage carries the structural relationships
    graph_out = body["trace"][3]["output"]
    assert any(e["relationship"] == "OCCURS_AT" for e in graph_out["edges"])
    assert any(e["relationship"] == "FEATURES" for e in graph_out["edges"])


def test_health_reports_ticketing_provider(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ticketing_provider", "mock")
    assert client.get("/health").json()["ticketing_provider"] == "mock"
