from fastapi import APIRouter, HTTPException, Query, status

from signal_service.adapters.google_trends import GoogleTrendsClient
from signal_service.adapters.musicbrainz import (
    MusicBrainzClient,
    MusicBrainzMatch,
    musicbrainz_observation,
)
from signal_service.classification import classification_observation, classify_channel
from signal_service.clients.entity_client import EntityServiceClient
from signal_service.clients.graph_client import GraphServiceClient
from signal_service.clients.observation_client import ObservationServiceClient
from signal_service.config import settings
from signal_service.graph_projection import project_entity_graph
from signal_service.identity import kg_mid_alias, mbid_alias
from signal_service.schemas import GoogleTrendsSignals, NormalizedObservation

router = APIRouter(prefix="/v1/signals/google-trends", tags=["google-trends"])


@router.get("/artists/{query}/preview", response_model=GoogleTrendsSignals)
async def preview_google_trends(query: str) -> GoogleTrendsSignals:
    """Fetch and normalize Google Trends signals for a query without persisting."""
    try:
        return await GoogleTrendsClient().fetch_query(query)
    except Exception as exc:  # noqa: BLE001 — surface provider errors to API consumer
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Google Trends fetch failed: {exc}",
        ) from exc


@router.post("/artists/{query}/ingest")
async def ingest_google_trends(
    query: str,
    trace: bool = Query(default=False, description="Return a per-stage PipelineTrace."),
) -> dict[str, object]:
    """Normalize Google Trends signals, classify + resolve the query, and populate the graph.

    Trends is a first-class classify->resolve pipeline: the query name is classified the same
    deterministic way YouTube channels are (MusicBrainz first), so a Trends query and a
    YouTube channel for the same act converge on one canonical entity. Its google_kg_mid then
    folds in as an alias on that entity, unifying identity across sources.
    """
    observation_client = ObservationServiceClient()

    try:
        signals = await GoogleTrendsClient().fetch_query(query)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Google Trends fetch failed: {exc}",
        ) from exc

    # Classify the query name (MusicBrainz first) so we resolve to the right KIND of entity,
    # exactly as the YouTube pipeline does — the shared classifier is what unifies the two.
    classification = await classify_channel(signals.query, None, MusicBrainzClient())
    classification_obs = classification_observation(signals.entity, classification)

    outbound = [*signals.observations, classification_obs]
    if classification.method.startswith("musicbrainz") and classification.mbid:
        outbound.append(
            musicbrainz_observation(
                signals.entity,
                MusicBrainzMatch(
                    classification.entity_type,
                    classification.mbid,
                    round(classification.confidence * 100),
                    signals.query,
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

    # Resolve to a canonical entity of the classified type (best-effort).
    resolution: dict[str, object] | None = None
    try:
        resolution = await EntityServiceClient().resolve(
            alias=signals.entity,
            entity_type=classification.entity_type,
            display_name=signals.query,
            source="google-trends",
            metadata={"google_query": signals.query},
        )
    except Exception:  # noqa: BLE001 — resolution is best-effort
        resolution = None

    canonical_id = resolution.get("canonical_id") if resolution else None

    # Fold the identity cross-references (Google KG mID + any MBID) in as aliases on the
    # canonical entity — this is the backbone that unifies Trends demand with the same act's
    # YouTube popularity (best-effort).
    kg_mid = _obs_value(signals.observations, "google_kg_mid")
    linked_aliases: list[str] = []
    if canonical_id:
        aliases = [kg_mid_alias(kg_mid)] if kg_mid else []
        if classification.mbid:
            aliases.append(mbid_alias(classification.mbid))
        if aliases:
            try:
                await EntityServiceClient().link_aliases(
                    canonical_id=str(canonical_id), aliases=aliases, source="google-trends"
                )
                linked_aliases = aliases
            except Exception:  # noqa: BLE001 — alias folding is best-effort
                linked_aliases = []

    # Graph projection keyed on the canonical entity so demand edges attach to the real act
    # (falls back to the source handle if resolution was unavailable).
    graph_node_id = str(canonical_id or signals.entity)
    projection = project_entity_graph(
        node_id=graph_node_id,
        entity_type=classification.entity_type,
        display_name=signals.query,
        observations=outbound,
    )
    graph_result: dict[str, int] | None = None
    try:
        graph_result = await GraphServiceClient().upsert_projection(projection)
    except Exception:  # noqa: BLE001 — graph projection is best-effort
        graph_result = None

    result: dict[str, object] = {
        "query": signals.query,
        "entity": signals.entity,
        "region": signals.region,
        "provider": signals.provider,
        "mock": signals.mock,
        "classification": {
            "entity_type": classification.entity_type,
            "confidence": classification.confidence,
            "method": classification.method,
            "needs_review": classification.needs_review,
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
                "service": f"signal-service / google-trends ({signals.provider})",
                "input": {"query": query, "region": signals.region},
                "output": [obs.to_payload() for obs in signals.observations],
                "added": [
                    "type-neutral source handle (google:query:*)",
                    "geographic distribution (within-pull)",
                    "momentum direction (not level)",
                    "rising related queries",
                    "google_kg_mid identity cross-reference",
                    "provenance envelope",
                ],
            },
            {
                "stage": "classification",
                "service": "signal-service / entity-classifier",
                "input": {"query": signals.query},
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
                    "display_name": signals.query,
                },
                "output": {
                    **(resolution or {"skipped": "entity-service unreachable"}),
                    "aliases_linked": linked_aliases,
                },
                "added": [
                    "canonical_id of the classified type",
                    "alias link (source handle -> canonical entity)",
                    "external ids folded in as aliases (kgmid:*, mbid:*) — the backbone",
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
                    "canonical entity node (google_kg_mid + momentum as properties)",
                    "STRONG_IN edges to region nodes (from geographic demand)",
                    "idempotent projection (no timestamps -> converges on re-ingest)",
                ],
            },
        ]

    return result


def _obs_value(observations: list[NormalizedObservation], attribute: str) -> object:
    for obs in observations:
        if obs.attribute == attribute:
            return obs.value
    return None
