from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from crawl_service.config import settings
from crawl_service.main import app


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


def test_entity_resolution_defaults_off():
    assert settings.entity_resolution_enabled is False


def test_run_disabled_returns_503(client: TestClient) -> None:
    assert client.post("/v1/internal/entity-resolution/run").status_code == 503


def test_reads_available_when_empty(client: TestClient) -> None:
    cov = client.get("/v1/internal/entity-resolution/coverage").json()
    assert cov["by_entity_type"]["ARTIST"]["mentions"] == 0
    unresolved = client.get("/v1/internal/entity-resolution/unresolved").json()
    assert unresolved["count"] == 0
    xi = client.get("/v1/internal/entity-resolution/cross-inventory?entity_type=ARTIST").json()
    assert xi["count"] == 0
    handles = client.get("/v1/internal/entities/ARTIST/artist:none/source-handles").json()
    assert handles["handles"] == []
    resolved = client.get("/v1/internal/events/event:none/resolved-entities").json()
    assert resolved["entities"] == []


def test_candidate_not_found(client: TestClient) -> None:
    assert client.get("/v1/internal/entity-resolution/candidates/nope").status_code == 404


def test_entity_resolution_source_set_folds_second_source():
    from crawl_service.config import Settings
    on = Settings(second_source_capture_enabled=True, second_source_name="district",
                  entity_resolution_sources="boshow")
    assert "district" in on.entity_resolution_source_set and "boshow" in on.entity_resolution_source_set
