from fastapi.testclient import TestClient


def test_register_artist(client: TestClient) -> None:
    response = client.post(
        "/v1/entities/artists",
        json={
            "display_name": "Daft Punk",
            "aliases": ["artist:spotify:4tZwfgrHOc3mvqYFCOCYO6"],
            "alias_source": "spotify",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] == "artist:daft-punk"
    assert body["entity_type"] == "artist"
    assert len(body["aliases"]) == 1


def test_resolve_spotify_alias_creates_canonical_artist(client: TestClient) -> None:
    spotify_id = "4tZwfgrHOc3mvqYFCOCYO6"
    response = client.post(
        f"/v1/entities/artists/resolve-spotify/{spotify_id}",
        json={"display_name": "Daft Punk"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["canonical_id"] == "artist:daft-punk"
    assert body["entity"]["display_name"] == "Daft Punk"


def test_resolve_is_idempotent(client: TestClient) -> None:
    payload = {
        "alias": "artist:spotify:abc123",
        "entity_type": "artist",
        "display_name": "Test Artist",
        "source": "spotify",
    }
    first = client.post("/v1/entities/resolve", json=payload)
    second = client.post("/v1/entities/resolve", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["canonical_id"] == second.json()["canonical_id"]
    assert first.json()["created"] is True
    assert second.json()["created"] is False


def test_lookup_by_alias(client: TestClient) -> None:
    alias = "artist:spotify:xyz789"
    client.post(
        "/v1/entities/resolve",
        json={
            "alias": alias,
            "entity_type": "artist",
            "display_name": "Alias Artist",
            "source": "spotify",
        },
    )

    response = client.get(f"/v1/entities/by-alias/{alias}")
    assert response.status_code == 200
    assert response.json()["display_name"] == "Alias Artist"


def test_get_entity_by_canonical_id(client: TestClient) -> None:
    client.post(
        "/v1/entities/artists",
        json={"display_name": "Phoenix", "aliases": [], "alias_source": "manual"},
    )
    response = client.get("/v1/entities/artist:phoenix")
    assert response.status_code == 200
    assert response.json()["display_name"] == "Phoenix"


def test_resolve_non_artist_entity_type(client: TestClient) -> None:
    """entity-service is generalized beyond artists (venues, events, organizers, ...)."""
    response = client.post(
        "/v1/entities/resolve",
        json={
            "alias": "venue:bookmyshow:antisocial-mumbai",
            "entity_type": "venue",
            "display_name": "antiSOCIAL Mumbai",
            "source": "bookmyshow",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is True
    assert body["canonical_id"] == "venue:antisocial-mumbai"
    assert body["entity"]["entity_type"] == "venue"


def test_resolve_without_display_name_returns_404(client: TestClient) -> None:
    response = client.post(
        "/v1/entities/resolve",
        json={
            "alias": "artist:spotify:missing",
            "entity_type": "artist",
            "create_if_missing": False,
        },
    )
    assert response.status_code == 404
