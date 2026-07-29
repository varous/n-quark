from fastapi import APIRouter, HTTPException, status

from signal_service.adapters.spotify import SpotifyClient
from signal_service.clients.observation_client import ObservationServiceClient
from signal_service.config import settings
from signal_service.schemas import SpotifyArtistSignals

router = APIRouter(prefix="/v1/signals/spotify", tags=["spotify"])


@router.get("/artists/{spotify_id}/preview", response_model=SpotifyArtistSignals)
async def preview_spotify_artist(spotify_id: str) -> SpotifyArtistSignals:
    """Fetch and normalize Spotify artist signals without persisting observations."""
    client = SpotifyClient()
    try:
        return await client.fetch_artist(spotify_id)
    except Exception as exc:  # noqa: BLE001 — surface provider errors to API consumer
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Spotify fetch failed: {exc}",
        ) from exc


@router.post("/artists/{spotify_id}/ingest")
async def ingest_spotify_artist(spotify_id: str) -> dict[str, object]:
    """Normalize Spotify artist signals and append observations (append-only)."""
    spotify = SpotifyClient()
    observation_client = ObservationServiceClient()

    try:
        signals = await spotify.fetch_artist(spotify_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Spotify fetch failed: {exc}",
        ) from exc

    try:
        persisted = await observation_client.append_observations(signals.observations)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Observation service write failed: {exc}",
        ) from exc

    return {
        "spotify_id": signals.spotify_id,
        "entity": signals.entity,
        "name": signals.name,
        "mock": signals.mock,
        "observation_service": settings.observation_service_url,
        "observations_created": len(persisted),
        "observations": persisted,
    }
