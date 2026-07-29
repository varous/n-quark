from fastapi import APIRouter, HTTPException, Query, status

from signal_service.adapters.youtube import YouTubeClient
from signal_service.clients.observation_client import ObservationServiceClient
from signal_service.config import settings
from signal_service.schemas import YouTubeChannelSignals

router = APIRouter(prefix="/v1/signals/youtube", tags=["youtube"])


@router.get("/channels/{channel_id}/preview", response_model=YouTubeChannelSignals)
async def preview_youtube_channel(channel_id: str) -> YouTubeChannelSignals:
    """Fetch and normalize YouTube channel signals without persisting observations."""
    client = YouTubeClient()
    try:
        return await client.fetch_channel(channel_id)
    except Exception as exc:  # noqa: BLE001 — surface provider errors to API consumer
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"YouTube fetch failed: {exc}",
        ) from exc


@router.post("/channels/{channel_id}/ingest")
async def ingest_youtube_channel(
    channel_id: str,
    trace: bool = Query(
        default=False,
        description="Return a per-stage PipelineTrace alongside the result.",
    ),
) -> dict[str, object]:
    """Normalize YouTube channel signals and append observations (append-only).

    With ``?trace=true`` the response also carries a ``trace`` array — one record per
    pipeline stage this endpoint performs — so the Pipeline Inspector can render the
    real transformation instead of a static sample.
    """
    youtube = YouTubeClient()
    observation_client = ObservationServiceClient()

    try:
        signals = await youtube.fetch_channel(channel_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"YouTube fetch failed: {exc}",
        ) from exc

    try:
        persisted = await observation_client.append_observations(signals.observations)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Observation service write failed: {exc}",
        ) from exc

    result: dict[str, object] = {
        "channel_id": signals.channel_id,
        "entity": signals.entity,
        "name": signals.name,
        "mock": signals.mock,
        "observation_service": settings.observation_service_url,
        "observations_created": len(persisted),
        "observations": persisted,
    }

    if trace:
        result["trace"] = [
            {
                "stage": "ingestion",
                "service": "signal-service / youtube adapter",
                "input": {"channel_id": channel_id},
                "output": [obs.to_payload() for obs in signals.observations],
                "added": [
                    "entity id",
                    "attribute mapping",
                    "source",
                    "confidence",
                    "provenance envelope",
                    "typed values (API strings -> int)",
                ],
            },
            {
                "stage": "observation",
                "service": "observation-service",
                "input": {"observations_in": len(signals.observations)},
                "output": persisted,
                "added": ["uuid", "created_at (server ingest time)", "append-only guarantee"],
            },
        ]

    return result
