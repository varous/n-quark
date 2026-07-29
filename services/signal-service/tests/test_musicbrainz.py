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


async def test_lookup_returns_both_label_and_artist_for_tseries() -> None:
    # Faithful to the real API: T-Series matches a label AND a coincidental same-named artist.
    lookup = await MusicBrainzClient().lookup("T-Series")
    assert lookup.label is not None
    assert lookup.label.entity_type == "label"
    assert lookup.artist is not None
    assert lookup.artist.entity_type == "artist"
    assert lookup.label.score == lookup.artist.score == 100


async def test_lookup_returns_artist_only_for_arijit() -> None:
    lookup = await MusicBrainzClient().lookup("Arijit Singh")
    assert lookup.artist is not None and lookup.artist.entity_type == "artist"
    assert lookup.label is None


async def test_lookup_returns_nothing_for_unknown() -> None:
    lookup = await MusicBrainzClient().lookup("Totally Unknown Xyz")
    assert lookup.label is None and lookup.artist is None


def test_musicbrainz_observation_carries_official_api_provenance() -> None:
    match = MusicBrainzMatch("label", "mbid-123", 100, "T-Series", "Distributor")
    obs = musicbrainz_observation("youtube:channel:abc", match)
    assert obs.attribute == "musicbrainz_id"
    assert obs.value == "mbid-123"
    assert obs.source == "musicbrainz"
    assert obs.metadata["provenance"]["acquisition_method"] == "official_api"
