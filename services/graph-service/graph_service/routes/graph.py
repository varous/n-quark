from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from graph_service.deps import get_store
from graph_service.schemas import (
    BatchResult,
    EdgeRead,
    EdgeUpsert,
    GraphBatch,
    GraphStats,
    NeighborRead,
    NeighborsResponse,
    NodeListResponse,
    NodeRead,
    NodeUpsert,
)
from graph_service.store import Edge, GraphStore, Node

router = APIRouter(prefix="/v1/graph", tags=["graph"])


@router.post("/nodes", response_model=NodeRead, summary="Upsert a canonical entity node")
def upsert_node(payload: NodeUpsert, store: GraphStore = Depends(get_store)) -> NodeRead:
    node = store.upsert_node(Node(payload.id, payload.type, payload.properties))
    return NodeRead(id=node.id, type=node.type, properties=node.properties)


@router.post("/edges", response_model=EdgeRead, summary="Upsert a directional relationship")
def upsert_edge(payload: EdgeUpsert, store: GraphStore = Depends(get_store)) -> EdgeRead:
    edge = store.upsert_edge(
        Edge(payload.source, payload.relationship, payload.target, payload.properties)
    )
    return EdgeRead(
        source=edge.source,
        relationship=edge.relationship,
        target=edge.target,
        properties=edge.properties,
    )


@router.post("/batch", response_model=BatchResult, summary="Upsert a projection (nodes + edges)")
def upsert_batch(payload: GraphBatch, store: GraphStore = Depends(get_store)) -> BatchResult:
    """Upsert a whole projection in one call. Nodes first so edges never dangle.

    The pipeline projects a resolved entity and its relationships as one batch; doing it in a
    single request keeps the projection atomic from the caller's view and avoids N round-trips.
    """
    for node in payload.nodes:
        store.upsert_node(Node(node.id, node.type, node.properties))
    for edge in payload.edges:
        store.upsert_edge(Edge(edge.source, edge.relationship, edge.target, edge.properties))
    return BatchResult(nodes=len(payload.nodes), edges=len(payload.edges))


@router.get("/stats", response_model=GraphStats, summary="Node and edge counts")
def graph_stats(store: GraphStore = Depends(get_store)) -> GraphStats:
    return GraphStats(**store.stats())


@router.get("/nodes", response_model=NodeListResponse, summary="List nodes, optionally by type")
def list_nodes(
    node_type: str | None = Query(default=None, alias="type"),
    limit: int = Query(default=100, ge=1, le=500),
    store: GraphStore = Depends(get_store),
) -> NodeListResponse:
    nodes = store.list_nodes(node_type, limit)
    return NodeListResponse(
        count=len(nodes),
        nodes=[NodeRead(id=n.id, type=n.type, properties=n.properties) for n in nodes],
    )


@router.get("/nodes/{node_id}", response_model=NodeRead, summary="Get a node by id")
def get_node(node_id: str, store: GraphStore = Depends(get_store)) -> NodeRead:
    node = store.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return NodeRead(id=node.id, type=node.type, properties=node.properties)


@router.get(
    "/nodes/{node_id}/neighbors",
    response_model=NeighborsResponse,
    summary="1-hop neighborhood of a node",
)
def node_neighbors(
    node_id: str,
    direction: Literal["out", "in", "both"] = Query(default="both"),
    relationship: str | None = Query(default=None),
    store: GraphStore = Depends(get_store),
) -> NeighborsResponse:
    neighbors = store.neighbors(node_id, direction=direction, relationship=relationship)
    return NeighborsResponse(
        node_id=node_id,
        count=len(neighbors),
        neighbors=[
            NeighborRead(
                relationship=n.relationship,
                direction=n.direction,
                node=NodeRead(id=n.node.id, type=n.node.type, properties=n.node.properties),
            )
            for n in neighbors
        ],
    )
