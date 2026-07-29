"""Spotify Web API adapter — normalizes artist signals into observations."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from signal_service.config import settings
from signal_service.schemas import NormalizedObservation, SpotifyArtistSignals

SIGNAL_SOURCE = "spotify"
ADAPTER_VERSION = "spotify-v1"
DEFAULT_MOCK_ARTIST_ID = "4tZwfgrHOc3mvqYFCOCYO6"  # Daft Punk


def entity_id_for_spotify_artist(spotify_id: str) -> str:
    return f"artist:spotify:{spotify_id}"


def normalize_artist_response(
    spotify_id: str,
    artist: dict[str, Any],
    *,
    mock: bool = False,
    fetched_at: datetime | None = None,
) -> SpotifyArtistSignals:
    """Convert Spotify artist JSON into normalized observation payloads."""
    when = fetched_at or datetime.now(UTC)
    entity = entity_id_for_spotify_artist(spotify_id)
    name = artist.get("name", "Unknown Artist")
    popularity = artist.get("popularity")
    genres = artist.get("genres", [])
    followers = (artist.get("followers") or {}).get("total")
    external_urls = artist.get("external_urls") or {}
    spotify_url = external_urls.get("spotify", f"https://open.spotify.com/artist/{spotify_id}")

    base_evidence = {
        "spotify_id": spotify_id,
        "spotify_url": spotify_url,
        "endpoint": f"/v1/artists/{spotify_id}",
        "fetched_at": when.isoformat(),
    }
    base_metadata = {
        "adapter": ADAPTER_VERSION,
        "signal_provider": SIGNAL_SOURCE,
        "mock": mock,
    }

    observations: list[NormalizedObservation] = [
        NormalizedObservation(
            entity=entity,
            attribute="display_name",
            value=name,
            source=SIGNAL_SOURCE,
            timestamp=when,
            confidence=0.99 if not mock else 0.5,
            evidence={**base_evidence, "field": "name"},
            metadata={**base_metadata, "normalization": "direct"},
        ),
    ]

    if popularity is not None:
        observations.append(
            NormalizedObservation(
                entity=entity,
                attribute="popularity",
                value=popularity,
                source=SIGNAL_SOURCE,
                timestamp=when,
                confidence=0.95 if not mock else 0.5,
                evidence={**base_evidence, "field": "popularity"},
                metadata={**base_metadata, "normalization": "direct", "scale": "0-100"},
            )
        )

    if genres:
        observations.append(
            NormalizedObservation(
                entity=entity,
                attribute="genres",
                value=genres,
                source=SIGNAL_SOURCE,
                timestamp=when,
                confidence=0.9 if not mock else 0.5,
                evidence={**base_evidence, "field": "genres"},
                metadata={**base_metadata, "normalization": "direct"},
            )
        )

    if followers is not None:
        observations.append(
            NormalizedObservation(
                entity=entity,
                attribute="follower_count",
                value=followers,
                source=SIGNAL_SOURCE,
                timestamp=when,
                confidence=0.95 if not mock else 0.5,
                evidence={**base_evidence, "field": "followers.total"},
                metadata={**base_metadata, "normalization": "direct"},
            )
        )

    return SpotifyArtistSignals(
        spotify_id=spotify_id,
        entity=entity,
        name=name,
        observations=observations,
        fetched_at=when,
        mock=mock,
    )


def mock_artist_payload(spotify_id: str) -> dict[str, Any]:
    """Deterministic demo payload when Spotify credentials are unavailable."""
    catalog: dict[str, dict[str, Any]] = {
        DEFAULT_MOCK_ARTIST_ID: {
            "id": DEFAULT_MOCK_ARTIST_ID,
            "name": "Daft Punk",
            "popularity": 83,
            "genres": ["french house", "electronic", "filter house"],
            "followers": {"total": 10_500_000},
            "external_urls": {
                "spotify": f"https://open.spotify.com/artist/{DEFAULT_MOCK_ARTIST_ID}"
            },
        },
    }
    if spotify_id in catalog:
        return catalog[spotify_id]
    return {
        "id": spotify_id,
        "name": f"Mock Artist ({spotify_id[:8]})",
        "popularity": 50,
        "genres": ["electronic"],
        "followers": {"total": 100_000},
        "external_urls": {"spotify": f"https://open.spotify.com/artist/{spotify_id}"},
    }


class SpotifyClient:
    def __init__(self) -> None:
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    async def fetch_artist(self, spotify_id: str) -> SpotifyArtistSignals:
        if settings.use_spotify_mock:
            artist = mock_artist_payload(spotify_id)
            return normalize_artist_response(spotify_id, artist, mock=True)

        token = await self._get_access_token()
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{settings.spotify_api_base}/artists/{spotify_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            return normalize_artist_response(spotify_id, response.json(), mock=False)

    async def _get_access_token(self) -> str:
        now = datetime.now(UTC)
        if self._access_token and self._token_expires_at and now < self._token_expires_at:
            return self._access_token

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                settings.spotify_token_url,
                data={"grant_type": "client_credentials"},
                auth=(settings.spotify_client_id, settings.spotify_client_secret),
            )
            response.raise_for_status()
            payload = response.json()

        self._access_token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 3600))
        self._token_expires_at = now.replace(microsecond=0) + timedelta(seconds=max(expires_in - 60, 0))
        return self._access_token
