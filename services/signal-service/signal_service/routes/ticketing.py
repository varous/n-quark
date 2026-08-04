from fastapi import APIRouter, HTTPException, Query, status

from signal_service.adapters.ticketing import (
    EventNotFound,
    TicketingClient,
    TicketingEvent,
    artist_handle,
    commercial_state,
    event_handle,
    normalize_event,
    venue_handle,
)
from signal_service.clients.entity_client import EntityServiceClient
from signal_service.clients.graph_client import GraphServiceClient
from signal_service.clients.observation_client import ObservationServiceClient
from signal_service.clients.shadow_client import ShadowLedgerClient
from signal_service.config import settings
from signal_service.graph_projection import project_ticketing_graph

router = APIRouter(prefix="/v1/signals/ticketing", tags=["ticketing"])


@router.get("/discover")
async def discover_events(
    city: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    source: str | None = Query(default=None, description="Provider override (e.g. boshow|district)."),
) -> dict[str, object]:
    """List event refs from the requested (or configured) ticketing provider (no persistence)."""
    try:
        refs = await TicketingClient(source).discover(city=city, limit=limit)
    except Exception as exc:  # noqa: BLE001 — surface provider errors to API consumer
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Ticketing discovery failed: {exc}"
        ) from exc
    return {"provider": source or settings.ticketing_provider, "count": len(refs), "event_refs": refs}


@router.get("/events/{event_ref}/preview")
async def preview_event(
    event_ref: str,
    source: str | None = Query(default=None, description="Provider override (e.g. boshow|district)."),
) -> dict[str, object]:
    """Fetch and normalize one event without persisting."""
    try:
        event = await TicketingClient(source).fetch_event(event_ref)
    except EventNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Ticketing fetch failed: {exc}"
        ) from exc
    return {**_event_summary(event), "observations": [o.to_payload() for o in normalize_event(event)]}


@router.post("/events/{event_ref}/ingest")
async def ingest_event(
    event_ref: str,
    trace: bool = Query(default=False, description="Return a per-stage PipelineTrace."),
    source: str | None = Query(default=None, description="Provider override (e.g. boshow|district)."),
) -> dict[str, object]:
    """Ingest a ticketing event: observations + event/venue/artist entities + graph edges.

    This is the first multi-entity adapter — one event yields an event, a venue, and a lineup
    of artists, plus the fill_ratio demand ground truth. Artists resolve by name into the same
    canonical entities the YouTube/Trends pipelines produce, so supply meets demand on one node.
    The optional ``source`` selects the provider per-request so the scheduler can capture Boshow
    and a second source (District) through one route.
    """
    ticketing = TicketingClient(source)
    observation_client = ObservationServiceClient()

    try:
        event = await ticketing.fetch_event(event_ref)
    except EventNotFound as exc:
        # Source reachable, record genuinely absent -> 404 so the scheduler records authoritative
        # absence rather than a source failure.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
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

    # Shadow Ledger (Phase 1): record the event's public commercial state so repeated ingests accrue
    # a transition history. Best-effort + feature-flagged; OFF by default so ingest behaviour is
    # unchanged unless explicitly enabled, and a failure here never breaks ingestion.
    shadow_attempted = (
        settings.shadow_ledger_enabled and event.source in settings.shadow_ledger_source_set
    )
    shadow_result: dict[str, object] | None = None
    if shadow_attempted:
        try:
            shadow_result = await ShadowLedgerClient().observe(
                canonical_event_id=event_cid,
                source_id=event.source,
                capture=commercial_state(event),
                source_record_id=event.source_event_id,
                provenance={"source_url": event.event_url},
                epistemic_status="observed_public_state",
            )
        except Exception:  # noqa: BLE001 — shadow ledger is best-effort, must not break ingest
            shadow_result = None

    result: dict[str, object] = {
        **_event_summary(event),
        "resolved": resolved,
        "graph": graph_result,
        "observation_service": settings.observation_service_url,
        "observations_created": len(persisted),
        "observations": persisted,
    }
    if shadow_attempted:
        result["shadow_ledger"] = shadow_result

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
        # Additive stage — only present when the Shadow Ledger is enabled for this source, so the
        # default trace shape (ingestion/observation/entity/graph) is preserved.
        if shadow_attempted:
            transitions = (shadow_result or {}).get("transitions") or []
            result["trace"].append(  # type: ignore[union-attr]
                {
                    "stage": "shadow_ledger",
                    "service": "graph-service / shadow-ledger (internal)",
                    "input": {"event_id": event_cid, "commercial_state": commercial_state(event)},
                    "output": (
                        {
                            "noop": (shadow_result or {}).get("noop"),
                            "transitions": [t.get("transition_type") for t in transitions],
                            "state_id": ((shadow_result or {}).get("state") or {}).get("id"),
                        }
                        if shadow_result is not None
                        else {"skipped": "shadow-ledger unreachable"}
                    ),
                    "added": [
                        "public commercial state captured as an immutable snapshot",
                        "deterministic transition detection vs previous state",
                        "fill_ratio tagged observed_public_state (never verified sell-through)",
                    ],
                }
            )

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
        "image_url": event.image_url,
        "provider": settings.ticketing_provider,
    }


def _graph_event_props(event: TicketingEvent) -> dict[str, object]:
    props: dict[str, object] = {
        "display_name": event.event_name,
        "category": event.category,
        "city": event.city,
        "source": event.source,  # canonical id is event:{slug}; carry source for the feed's tier
        "source_url": event.event_url,  # crawl-space link-out target for link_only events
    }
    if event.curator:
        props["organizer"] = event.curator  # name only — organizers aren't canonical entities yet
    if event.starts_at is not None:
        props["starts_at"] = event.starts_at.isoformat()
    if event.price_min is not None:
        props["price_min"] = event.price_min
        props["currency"] = event.currency
    if event.fill_ratio is not None:
        props["fill_ratio"] = event.fill_ratio
        props["tickets_sold"] = event.tickets_sold
        props["capacity"] = event.capacity
    if event.image_url:
        props["image_url"] = event.image_url
    props["verified"] = event.verified
    return props
