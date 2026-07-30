from fastapi import APIRouter, HTTPException, Query, status

from signal_service.adapters.google_trends import GoogleTrendsClient
from signal_service.clients.graph_client import GraphServiceClient
from signal_service.clients.observation_client import ObservationServiceClient
from signal_service.config import settings
from signal_service.graph_projection import project_entity_graph
from signal_service.schemas import GoogleTrendsSignals

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
    """Normalize Google Trends signals and append observations (append-only)."""
    observation_client = ObservationServiceClient()

    try:
        signals = await GoogleTrendsClient().fetch_query(query)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Google Trends fetch failed: {exc}",
        ) from exc

    try:
        persisted = await observation_client.append_observations(signals.observations)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Observation service write failed: {exc}",
        ) from exc

    # Graph projection: Trends is not classified/resolved, so the demand signal projects onto
    # the query handle as a search_topic node with STRONG_IN region edges. Once the query's
    # google_kg_mid folds into a canonical entity as an alias, this demand unifies onto it.
    projection = project_entity_graph(
        node_id=signals.entity,
        entity_type="search_topic",
        display_name=signals.query,
        observations=signals.observations,
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
                "stage": "observation",
                "service": "observation-service",
                "input": {"observations_in": len(signals.observations)},
                "output": persisted,
                "added": ["uuid", "created_at (server ingest time)", "append-only guarantee"],
            },
            {
                "stage": "graph",
                "service": "graph-service",
                "input": {"node_id": signals.entity, "observations_in": len(signals.observations)},
                "output": (
                    projection.to_payload()
                    if graph_result is not None
                    else {"skipped": "graph-service unreachable"}
                ),
                "added": [
                    "search_topic node (google_kg_mid + momentum as properties)",
                    "STRONG_IN edges to region nodes (from geographic demand)",
                    "idempotent projection (no timestamps -> converges on re-ingest)",
                ],
            },
        ]

    return result
