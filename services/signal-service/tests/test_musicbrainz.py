import pytest

from signal_service.adapters.musicbrainz import (
    MusicBrainzClient,
    MusicBrainzMatch,
    musicbrainz_observation,
)
from signal_service.config import settings


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "musicbrainz_mock_mode", True)
    MusicBrainzClient._cache.clear()


async def test_classify_name_returns_label_for_tseries() -> None:
    match = await MusicBrainzClient().classify_name("T-Series")
    assert match is not None
    assert match.entity_type == "label"
    assert match.mbid


async def test_classify_name_returns_artist_for_arijit() -> None:
    match = await MusicBrainzClient().classify_name("Arijit Singh")
    assert match is not None
    assert match.entity_type == "artist"


async def test_classify_name_returns_none_for_unknown() -> None:
    assert await MusicBrainzClient().classify_name("Totally Unknown Xyz") is None


def test_musicbrainz_observation_carries_official_api_provenance() -> None:
    match = MusicBrainzMatch("label", "mbid-123", 100, "T-Series")
    obs = musicbrainz_observation("youtube:channel:abc", match)
    assert obs.attribute == "musicbrainz_id"
    assert obs.value == "mbid-123"
    assert obs.source == "musicbrainz"
    assert obs.metadata["provenance"]["acquisition_method"] == "official_api"
