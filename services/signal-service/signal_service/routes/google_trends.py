from fastapi import APIRouter, HTTPException, Query, status

from signal_service.adapters.google_trends import GoogleTrendsClient
from signal_service.clients.observation_client import ObservationServiceClient
from signal_service.config import settings
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

    result: dict[str, object] = {
        "query": signals.query,
        "entity": signals.entity,
        "region": signals.region,
        "provider": signals.provider,
        "mock": signals.mock,
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
        ]

    return result
