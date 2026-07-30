from typing import Any

from pydantic import BaseModel, Field


class NodeUpsert(BaseModel):
    id: str = Field(min_length=1, max_length=512)
    type: str = Field(default="unknown", max_length=64)
    properties: dict[str, Any] = Field(default_factory=dict)


class EdgeUpsert(BaseModel):
    source: str = Field(min_length=1, max_length=512)
    relationship: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=512)
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphBatch(BaseModel):
    """A projection: a set of nodes and edges upserted together (idempotent)."""

    nodes: list[NodeUpsert] = Field(default_factory=list)
    edges: list[EdgeUpsert] = Field(default_factory=list)


class BatchResult(BaseModel):
    nodes: int
    edges: int


class NodeRead(BaseModel):
    id: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class EdgeRead(BaseModel):
    source: str
    relationship: str
    target: str
    properties: dict[str, Any] = Field(default_factory=dict)


class NeighborRead(BaseModel):
    relationship: str
    direction: str
    node: NodeRead


class NeighborsResponse(BaseModel):
    node_id: str
    count: int
    neighbors: list[NeighborRead]


class GraphStats(BaseModel):
    nodes: int
    edges: int
