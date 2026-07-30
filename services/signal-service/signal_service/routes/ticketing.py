from fastapi import APIRouter, HTTPException, Query, status

from signal_service.adapters.ticketing import (
    TicketingClient,
    TicketingEvent,
    artist_handle,
    event_handle,
    normalize_event,
    venue_handle,
)
from signal_service.clients.entity_client import EntityServiceClient
from signal_service.clients.graph_client import GraphServiceClient
from signal_service.clients.observation_client import ObservationServiceClient
from signal_service.config import settings
from signal_service.graph_projection import project_ticketing_graph

router = APIRouter(prefix="/v1/signals/ticketing", tags=["ticketing"])


@router.get("/discover")
async def discover_events(
    city: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, object]:
    """List event refs from the configured ticketing provider (discovery, no persistence)."""
    try:
        refs = await TicketingClient().discover(city=city, limit=limit)
    except Exception as exc:  # noqa: BLE001 — surface provider errors to API consumer
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Ticketing discovery failed: {exc}"
        ) from exc
    return {"provider": settings.ticketing_provider, "count": len(refs), "event_refs": refs}


@router.get("/events/{event_ref}/preview")
async def preview_event(event_ref: str) -> dict[str, object]:
    """Fetch and normalize one event without persisting."""
    try:
        event = await TicketingClient().fetch_event(event_ref)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Ticketing fetch failed: {exc}"
        ) from exc
    return {**_event_summary(event), "observations": [o.to_payload() for o in normalize_event(event)]}


@router.post("/events/{event_ref}/ingest")
async def ingest_event(
    event_ref: str,
    trace: bool = Query(default=False, description="Return a per-stage PipelineTrace."),
) -> dict[str, object]:
    """Ingest a ticketing event: observations + event/venue/artist entities + graph edges.

    This is the first multi-entity adapter — one event yields an event, a venue, and a lineup
    of artists, plus the fill_ratio demand ground truth. Artists resolve by name into the same
    canonical entities the YouTube/Trends pipelines produce, so supply meets demand on one node.
    """
    ticketing = TicketingClient()
    observation_client = ObservationServiceClient()

    try:
        event = await ticketing.fetch_event(event_ref)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Ticketing fetch failed: {exc}"
        ) from exc

    observations = normalize_event(event)
    try:
        persisted = await observation_client.append_observations(observations)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Observation service write failed: {exc}"
        ) from exc

    # Resolve the event, its venue, and each performer to canonical entities (best-effort).
    entity_client = EntityServiceClient()
    resolved: dict[str, object] = {}

    async def _resolve(alias: str, entity_type: str, display_name: str) -> str:
        try:
            res = await entity_client.resolve(
                alias=alias, entity_type=entity_type, display_name=display_name,
                source=event.source, metadata={"ticketing_source": event.source},
            )
            return str(res.get("canonical_id") or alias)
        except Exception:  # noqa: BLE001 — resolution is best-effort
            return alias

    event_cid = await _resolve(event_handle(event), "event", event.event_name)
    venue_cid = (
        await _resolve(venue_handle(event), "venue", event.venue_name) if event.venue_name else None
    )
    artist_pairs: list[tuple[str, str]] = []
    for i, name in enumerate(event.artists):
        cid = await _resolve(artist_handle(event, name, i), "artist", name)
        artist_pairs.append((cid, name))
    resolved = {"event": event_cid, "venue": venue_cid, "artists": [c for c, _ in artist_pairs]}

    # Graph projection: the first structural edges (OCCURS_AT / FEATURES / IN_REGION).
    projection = project_ticketing_graph(
        event_id=event_cid,
        event_properties=_graph_event_props(event),
        venue_id=venue_cid,
        venue_name=event.venue_name or None,
        artists=artist_pairs,
        region=event.region or None,
    )
    graph_result: dict[str, int] | None = None
    try:
        graph_result = await GraphServiceClient().upsert_projection(projection)
    except Exception:  # noqa: BLE001 — graph projection is best-effort
        graph_result = None

    result: dict[str, object] = {
        **_event_summary(event),
        "resolved": resolved,
        "graph": graph_result,
        "observation_service": settings.observation_service_url,
        "observations_created": len(persisted),
        "observations": persisted,
    }

    if trace:
        result["trace"] = [
            {
                "stage": "ingestion",
                "service": f"signal-service / ticketing ({event.source})",
                "input": {"event_ref": event_ref},
                "output": [o.to_payload() for o in observations],
                "added": [
                    "type-neutral source handles (event / venue / artist)",
                    "fill_ratio = tickets_sold / capacity (demand ground truth)",
                    "relationship observations (lineup, venue, region)",
                    "public_scrape provenance envelope (logged_out + robots_respected)",
                ],
            },
            {
                "stage": "observation",
                "service": "observation-service",
                "input": {"observations_in": len(observations)},
                "output": persisted,
                "added": ["uuid", "created_at (server ingest time)", "append-only guarantee"],
            },
            {
                "stage": "entity",
                "service": "entity-service",
                "input": {"event": event_handle(event), "venue": event.venue_name, "artists": event.artists},
                "output": resolved,
                "added": [
                    "canonical event + venue + artist entities",
                    "artists resolve by name -> unify with YouTube/Trends artist entities",
                ],
            },
            {
                "stage": "graph",
                "service": "graph-service",
                "input": {"event_id": event_cid, "observations_in": len(observations)},
                "output": (
                    projection.to_payload() if graph_result is not None
                    else {"skipped": "graph-service unreachable"}
                ),
                "added": [
                    "structural edges: OCCURS_AT (venue), FEATURES (artists), IN_REGION",
                    "event node carries fill_ratio + price + date as properties",
                    "idempotent projection (no timestamps -> converges on re-ingest)",
                ],
            },
        ]

    return result


def _event_summary(event: TicketingEvent) -> dict[str, object]:
    return {
        "source": event.source,
        "event_ref": event.event_slug,
        "event_name": event.event_name,
        "category": event.category,
        "venue": event.venue_name,
        "city": event.city,
        "region": event.region,
        "artists": event.artists,
        "price_min": event.price_min,
        "currency": event.currency,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "tickets_sold": event.tickets_sold,
        "capacity": event.capacity,
        "fill_ratio": event.fill_ratio,
        "provider": settings.ticketing_provider,
    }


def _graph_event_props(event: TicketingEvent) -> dict[str, object]:
    props: dict[str, object] = {
        "display_name": event.event_name,
        "category": event.category,
        "city": event.city,
    }
    if event.starts_at is not None:
        props["starts_at"] = event.starts_at.isoformat()
    if event.price_min is not None:
        props["price_min"] = event.price_min
        props["currency"] = event.currency
    if event.fill_ratio is not None:
        props["fill_ratio"] = event.fill_ratio
        props["tickets_sold"] = event.tickets_sold
        props["capacity"] = event.capacity
    props["verified"] = event.verified
    return props
