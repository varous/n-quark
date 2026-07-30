from signal_service.graph_projection import project_entity_graph
from signal_service.schemas import NormalizedObservation


def _obs(attribute: str, value: object) -> NormalizedObservation:
    return NormalizedObservation(
        entity="youtube:channel:abc", attribute=attribute, value=value, confidence=0.7
    )


def test_projects_identity_and_popularity_as_node_properties() -> None:
    obs = [
        _obs("subscriber_count", 285_000_000),
        _obs("video_count", 20_000),
        _obs("musicbrainz_id", "d8067fa7"),
        _obs("google_kg_mid", "/m/0abc"),
    ]
    projection = project_entity_graph(
        node_id="label:t-series", entity_type="label", display_name="T-Series", observations=obs
    )
    assert len(projection.nodes) == 1
    props = projection.nodes[0].properties
    assert props["display_name"] == "T-Series"
    assert props["mbid"] == "d8067fa7"
    assert props["google_kg_mid"] == "/m/0abc"
    assert props["subscriber_count"] == 285_000_000
    assert props["video_count"] == 20_000
    assert not projection.edges


def test_top_regions_become_ranked_strong_in_edges() -> None:
    obs = [_obs("search_top_regions", ["Delhi", "Maharashtra", "Punjab"])]
    projection = project_entity_graph(
        node_id="artist:diljit-dosanjh",
        entity_type="artist",
        display_name="Diljit Dosanjh",
        observations=obs,
    )
    # one primary node + three region nodes
    assert {n.type for n in projection.nodes} == {"artist", "region"}
    region_ids = [n.id for n in projection.nodes if n.type == "region"]
    assert region_ids == ["region:delhi", "region:maharashtra", "region:punjab"]
    edges = projection.edges
    assert all(e.relationship == "STRONG_IN" and e.source == "artist:diljit-dosanjh" for e in edges)
    assert [e.properties["rank"] for e in edges] == [1, 2, 3]


def test_classification_context_and_related_ride_along_as_properties() -> None:
    classification = NormalizedObservation(
        entity="youtube:channel:abc",
        attribute="candidate_entity_type",
        value="label",
        confidence=0.72,
        evidence={"method": "musicbrainz+tiebreak", "needs_review": True},
    )
    obs = [classification, _obs("related_rising_queries", ["a", "b", "c", "d", "e", "f", "g"])]
    projection = project_entity_graph(
        node_id="label:t-series", entity_type="label", display_name="T-Series", observations=obs
    )
    props = projection.nodes[0].properties
    assert props["classification_method"] == "musicbrainz+tiebreak"
    assert props["needs_review"] is True
    assert props["rising_related"] == ["a", "b", "c", "d", "e"]  # capped at 5


def test_projection_is_idempotent_no_timestamps() -> None:
    obs = [_obs("search_top_regions", ["Delhi"])]
    a = project_entity_graph(node_id="x", entity_type="artist", display_name="X", observations=obs)
    b = project_entity_graph(node_id="x", entity_type="artist", display_name="X", observations=obs)
    assert a.to_payload() == b.to_payload()
