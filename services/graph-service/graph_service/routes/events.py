"""Consumer events feed — the read model crawl-space syncs from.

Assembles each event from the graph (node props + OCCURS_AT venue / IN_REGION region / FEATURES
artists), tags it with a redistribution tier (policy lives server-side), and serves a filterable,
paginated feed. Excludes the ``excluded`` tier by default so a consumer never receives spam.
"""

from fastapi import APIRouter, Depends, Query

from graph_service.deps import get_store
from graph_service.redistribution import EXCLUDED, redistribution_tier
from graph_service.schemas import EventFeedItem, EventFeedResponse
from graph_service.store import GraphStore

router = APIRouter(prefix="/v1/events", tags=["events"])

_MAX_SCAN = 1000  # events scanned before filtering/paging (catalog is small; simple + correct)


def _source_of(event_id: str) -> str:
    return event_id.split(":", 1)[0] if ":" in event_id else ""


def _feed_item(node, store: GraphStore) -> EventFeedItem:
    p = node.properties
    # Canonical event ids are event:{slug}, so the source is carried as a node property by the
    # projection; fall back to the id prefix (source handles / tests) when it isn't.
    source = p.get("source") or _source_of(node.id)
    price = p.get("price_min")
    verified = bool(p.get("verified", False))
    venue = region = None
    artists: list[str] = []
    for nb in store.neighbors(node.id, direction="out"):
        label = nb.node.properties.get("display_name") or nb.node.id
        if nb.relationship == "OCCURS_AT":
            venue = label
        elif nb.relationship == "IN_REGION":
            region = label
        elif nb.relationship == "FEATURES":
            artists.append(label)
    return EventFeedItem(
        id=node.id, name=p.get("display_name"), category=p.get("category"), city=p.get("city"),
        region=region, venue=venue, artists=artists, starts_at=p.get("starts_at"),
        price_min=price, currency=p.get("currency"), is_free=(price == 0),
        fill_ratio=p.get("fill_ratio"), image_url=p.get("image_url"), source=source,
        source_url=p.get("source_url"),
        redistribution_tier=redistribution_tier(source, price, verified),
        updated_at=p.get("updated_at"),
    )


@router.get("", response_model=EventFeedResponse, summary="Tiered events feed for consumers")
def list_events(
    tier: str | None = Query(default=None, description="open | link_only | excluded"),
    free: bool | None = Query(default=None),
    source: str | None = Query(default=None),
    city: str | None = Query(default=None),
    updated_since: str | None = Query(default=None, description="ISO timestamp for incremental sync"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    store: GraphStore = Depends(get_store),
) -> EventFeedResponse:
    items = [_feed_item(n, store) for n in store.list_nodes("event", limit=_MAX_SCAN)]

    if tier:
        items = [i for i in items if i.redistribution_tier == tier]
    else:
        items = [i for i in items if i.redistribution_tier != EXCLUDED]  # never leak spam by default
    if free is not None:
        items = [i for i in items if i.is_free == free]
    if source:
        items = [i for i in items if i.source == source]
    if city:
        items = [i for i in items if (i.city or "").lower() == city.lower()]
    if updated_since:
        items = [i for i in items if i.updated_at and i.updated_at >= updated_since]

    items.sort(key=lambda i: i.updated_at or "", reverse=True)
    total = len(items)
    return EventFeedResponse(count=total, limit=limit, offset=offset, events=items[offset : offset + limit])
