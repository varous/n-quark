from fastapi import APIRouter, Body, HTTPException, Query, status

from signal_service.adapters.musicbrainz import (
    MusicBrainzClient,
    MusicBrainzMatch,
    musicbrainz_observation,
)
from signal_service.adapters.youtube import YouTubeClient
from signal_service.classification import classification_observation, classify_channel
from signal_service.clients.entity_client import EntityServiceClient
from signal_service.clients.graph_client import GraphServiceClient
from signal_service.clients.observation_client import ObservationServiceClient
from signal_service.config import settings
from signal_service.graph_projection import project_entity_graph
from signal_service.identity import mbid_alias
from signal_service.schemas import (
    YouTubeChannelReference,
    YouTubeChannelSignals,
    YouTubeChannelVerification,
    YouTubeSearchResult,
    YouTubeVideoBatchResult,
    YouTubeVideoSignals,
)

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


@router.get("/search", response_model=YouTubeSearchResult)
async def search_youtube_channels(
    q: str = Query(min_length=1, max_length=256, description="Artist name to discover a channel for."),
    limit: int = Query(default=5, ge=1, le=25),
) -> YouTubeSearchResult:
    """Bounded channel search for identity discovery (Phase 5A). Acquisition-only, no persistence —
    the demand layer resolves identity from these candidates."""
    try:
        return await YouTubeClient().search_channels(q, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"YouTube search failed: {exc}") from exc


@router.get("/channels/{channel_id}/verify", response_model=YouTubeChannelVerification)
async def verify_youtube_channel(channel_id: str) -> YouTubeChannelVerification:
    """Authoritative channels.list existence check for a known channel id (Phase 5A.1a). Returns
    FOUND / CHANNEL_NOT_FOUND (200); a provider/network failure returns 502 (never NOT_FOUND)."""
    try:
        return await YouTubeClient().verify_channel(channel_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"YouTube channel verification failed: {exc}") from exc


@router.get("/resolve/handle/{handle}", response_model=YouTubeChannelReference)
async def resolve_youtube_handle(handle: str) -> YouTubeChannelReference:
    """Map a @handle to its owning channel id (channels.list forHandle; Phase 5B.1). Acquisition-only —
    the caller must still run the authoritative /verify check on the returned id. 200 FOUND/NOT_FOUND;
    a provider/network failure returns 502."""
    try:
        return await YouTubeClient().resolve_channel_by_handle(handle)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"YouTube handle resolution failed: {exc}") from exc


@router.get("/resolve/video/{video_id}", response_model=YouTubeChannelReference)
async def resolve_youtube_video(video_id: str) -> YouTubeChannelReference:
    """Map a video id to the channel that published it (videos.list snippet.channelId; Phase 5B.1).
    Acquisition-only — the caller must still verify the returned channel id. 200 FOUND/NOT_FOUND;
    a provider/network failure returns 502."""
    try:
        return await YouTubeClient().resolve_channel_by_video(video_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"YouTube video resolution failed: {exc}") from exc


@router.get("/channels/{channel_id}/videos/preview", response_model=YouTubeVideoSignals)
async def preview_youtube_videos(
    channel_id: str,
    limit: int = Query(default=5, ge=1, le=50),
) -> YouTubeVideoSignals:
    """Recent uploaded-video stats by known channel id (Phase 5A). Acquisition-only, no persistence."""
    try:
        return await YouTubeClient().fetch_recent_videos(channel_id, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"YouTube video fetch failed: {exc}") from exc


@router.post("/videos/batch", response_model=YouTubeVideoBatchResult)
async def batch_youtube_video_stats(
    video_ids: list[str] = Body(..., embed=True, max_length=50,
                                description="Known video ids (≤50) to fetch statistics for."),
) -> YouTubeVideoBatchResult:
    """Batch statistics for known video ids via videos.list (Phase 5A.3). Quota-efficient known-id read
    (1 unit / 50 ids). Acquisition-only, no persistence; a provider failure returns 502."""
    try:
        return await YouTubeClient().fetch_video_stats_batch(video_ids)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail=f"YouTube batch stats failed: {exc}") from exc


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
    # Pass the real fetched video count through so the aggregator signal works live too.
    video_count = next(
        (obs.value for obs in signals.observations if obs.attribute == "video_count"),
        None,
    )
    raw = {"statistics": {"videoCount": video_count}} if video_count is not None else None
    classification = await classify_channel(signals.name, raw, MusicBrainzClient())
    classification_obs = classification_observation(signals.entity, classification)

    outbound = [*signals.observations, classification_obs]
    if classification.method.startswith("musicbrainz") and classification.mbid:
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

    canonical_id = resolution.get("canonical_id") if resolution else None

    # Fold the identity cross-reference (MusicBrainz MBID) in as an alias on the canonical
    # entity, so the act is reachable by its MBID and later unifies with other pipelines
    # (best-effort — the backbone link must not fail append-only ingestion).
    linked_aliases: list[str] = []
    if canonical_id and classification.mbid:
        aliases = [mbid_alias(classification.mbid)]
        try:
            await EntityServiceClient().link_aliases(
                canonical_id=str(canonical_id), aliases=aliases, source="musicbrainz"
            )
            linked_aliases = aliases
        except Exception:  # noqa: BLE001 — alias folding is best-effort
            linked_aliases = []

    # Graph projection: fold the resolved entity + its enrichment observations into the
    # knowledge graph (best-effort — a graph outage must not fail append-only ingestion).
    # Keyed by the canonical id when resolution succeeded, else the source handle so the
    # projection still lands and unifies later once the handle folds into the entity.
    graph_node_id = str(canonical_id or signals.entity)
    projection = project_entity_graph(
        node_id=graph_node_id,
        entity_type=classification.entity_type,
        display_name=signals.name,
        observations=outbound,
    )
    graph_result: dict[str, int] | None = None
    try:
        graph_result = await GraphServiceClient().upsert_projection(projection)
    except Exception:  # noqa: BLE001 — graph projection is best-effort
        graph_result = None

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
        "canonical_id": canonical_id,
        "aliases_linked": linked_aliases,
        "graph": graph_result,
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
                "output": {
                    **(resolution or {"skipped": "entity-service unreachable"}),
                    "aliases_linked": linked_aliases,
                },
                "added": [
                    "canonical_id of the classified type",
                    "alias link (source handle -> canonical entity)",
                    "external id folded in as alias (mbid:*) — the cross-source backbone",
                ],
            },
            {
                "stage": "graph",
                "service": "graph-service",
                "input": {"node_id": graph_node_id, "observations_in": len(outbound)},
                "output": (
                    projection.to_payload()
                    if graph_result is not None
                    else {"skipped": "graph-service unreachable"}
                ),
                "added": [
                    "canonical entity node (identity + popularity as properties)",
                    "STRONG_IN edges to region nodes (from geographic demand)",
                    "idempotent projection (no timestamps -> converges on re-ingest)",
                ],
            },
        ]

    return result
