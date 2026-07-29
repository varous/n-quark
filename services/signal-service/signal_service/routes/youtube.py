from fastapi import APIRouter, HTTPException, Query, status

from signal_service.adapters.musicbrainz import (
    MusicBrainzClient,
    MusicBrainzMatch,
    musicbrainz_observation,
)
from signal_service.adapters.youtube import YouTubeClient, mock_channel_payload
from signal_service.classification import classification_observation, classify_channel
from signal_service.clients.entity_client import EntityServiceClient
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

    # Entity classification runs ahead of resolution: infer what KIND of thing this channel
    # is (artist / label / ...) via MusicBrainz cross-reference, so a label is never resolved
    # as an artist. Heuristics are the fallback when MusicBrainz has no confident match.
    raw = mock_channel_payload(channel_id) if settings.use_youtube_mock else None
    classification = await classify_channel(signals.name, raw, MusicBrainzClient())
    classification_obs = classification_observation(signals.entity, classification)

    outbound = [*signals.observations, classification_obs]
    if classification.method == "musicbrainz" and classification.mbid:
        # Backbone enrichment: attach the canonical MusicBrainz id to the entity.
        outbound.append(
            musicbrainz_observation(
                signals.entity,
                MusicBrainzMatch(
                    classification.entity_type,
                    classification.mbid,
                    round(classification.confidence * 100),
                    signals.name,
                ),
            )
        )
    try:
        persisted = await observation_client.append_observations(outbound)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Observation service write failed: {exc}",
        ) from exc

    # Resolve to a canonical entity of the *classified* type (best-effort — a resolution
    # outage must not fail signal ingestion, which is the append-only source of truth).
    resolution: dict[str, object] | None = None
    try:
        resolution = await EntityServiceClient().resolve(
            alias=signals.entity,
            entity_type=classification.entity_type,
            display_name=signals.name,
            source="youtube",
            metadata={"youtube_channel_id": channel_id},
        )
    except Exception:  # noqa: BLE001 — resolution is best-effort
        resolution = None

    result: dict[str, object] = {
        "channel_id": signals.channel_id,
        "source_entity": signals.entity,
        "name": signals.name,
        "mock": signals.mock,
        "classification": {
            "entity_type": classification.entity_type,
            "confidence": classification.confidence,
            "method": classification.method,
            "needs_review": classification.needs_review,
            "reasons": classification.reasons,
        },
        "canonical_id": resolution.get("canonical_id") if resolution else None,
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
                    "type-neutral source handle (youtube:channel:*)",
                    "attribute mapping",
                    "source",
                    "confidence",
                    "provenance envelope",
                    "typed values (API strings -> int)",
                ],
            },
            {
                "stage": "classification",
                "service": "signal-service / entity-classifier",
                "input": {"channel_id": channel_id, "name": signals.name},
                "output": classification_obs.to_payload(),
                "added": [
                    f"inferred entity_type = {classification.entity_type}",
                    f"confidence = {classification.confidence}",
                    f"method = {classification.method}",
                    "reasons + needs_review flag",
                ],
            },
            {
                "stage": "observation",
                "service": "observation-service",
                "input": {"observations_in": len(outbound)},
                "output": persisted,
                "added": ["uuid", "created_at (server ingest time)", "append-only guarantee"],
            },
            {
                "stage": "entity",
                "service": "entity-service",
                "input": {
                    "alias": signals.entity,
                    "entity_type": classification.entity_type,
                    "display_name": signals.name,
                },
                "output": resolution or {"skipped": "entity-service unreachable"},
                "added": [
                    "canonical_id of the classified type",
                    "alias link (source handle -> canonical entity)",
                ],
            },
        ]

    return result
