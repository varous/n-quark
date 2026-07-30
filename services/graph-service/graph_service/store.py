"""Graph store abstraction.

The knowledge graph runs on Neo4j in production, but tests and offline dev use an
in-memory store — the same mock-first pattern as the signal adapters. Both implement the
GraphStore protocol, so routes never depend on Neo4j being up.

Nodes are canonical entities (id like ``artist:t-series``); relationships are directional
and idempotent (MERGE semantics). Relationship types are sanitised to UPPER_SNAKE so they
can be inlined into Cypher safely.
"""

import re
from dataclasses import dataclass, field
from typing import Literal, Protocol

_REL_RE = re.compile(r"[^A-Z0-9_]")
Direction = Literal["out", "in", "both"]


def normalize_rel(relationship: str) -> str:
    """Sanitise a relationship type to a safe UPPER_SNAKE token (e.g. signed_to -> SIGNED_TO)."""
    token = _REL_RE.sub("_", relationship.strip().upper()).strip("_")
    return token or "RELATED_TO"


@dataclass
class Node:
    id: str
    type: str = "unknown"
    properties: dict = field(default_factory=dict)


@dataclass
class Edge:
    source: str
    relationship: str
    target: str
    properties: dict = field(default_factory=dict)


@dataclass
class Neighbor:
    relationship: str
    direction: Direction
    node: Node


class GraphStore(Protocol):
    def upsert_node(self, node: Node) -> Node: ...
    def upsert_edge(self, edge: Edge) -> Edge: ...
    def get_node(self, node_id: str) -> Node | None: ...
    def neighbors(
        self, node_id: str, direction: Direction = "both", relationship: str | None = None
    ) -> list[Neighbor]: ...
    def stats(self) -> dict[str, int]: ...


class InMemoryGraphStore:
    """Hermetic store for tests / offline dev."""

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._edges: list[Edge] = []

    def upsert_node(self, node: Node) -> Node:
        existing = self._nodes.get(node.id)
        if existing is not None:
            if node.type and node.type != "unknown":
                existing.type = node.type
            existing.properties.update(node.properties)
            return existing
        created = Node(node.id, node.type or "unknown", dict(node.properties))
        self._nodes[node.id] = created
        return created

    def upsert_edge(self, edge: Edge) -> Edge:
        rel = normalize_rel(edge.relationship)
        # MERGE endpoints as thin nodes if they don't exist yet.
        self._nodes.setdefault(edge.source, Node(edge.source))
        self._nodes.setdefault(edge.target, Node(edge.target))
        for existing in self._edges:
            if (
                existing.source == edge.source
                and existing.relationship == rel
                and existing.target == edge.target
            ):
                existing.properties.update(edge.properties)
                return existing
        created = Edge(edge.source, rel, edge.target, dict(edge.properties))
        self._edges.append(created)
        return created

    def get_node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def neighbors(
        self, node_id: str, direction: Direction = "both", relationship: str | None = None
    ) -> list[Neighbor]:
        rel = normalize_rel(relationship) if relationship else None
        out: list[Neighbor] = []
        for edge in self._edges:
            if rel is not None and edge.relationship != rel:
                continue
            if direction in ("out", "both") and edge.source == node_id:
                target = self._nodes.get(edge.target)
                if target is not None:
                    out.append(Neighbor(edge.relationship, "out", target))
            if direction in ("in", "both") and edge.target == node_id:
                source = self._nodes.get(edge.source)
                if source is not None:
                    out.append(Neighbor(edge.relationship, "in", source))
        return out

    def stats(self) -> dict[str, int]:
        return {"nodes": len(self._nodes), "edges": len(self._edges)}


class Neo4jGraphStore:
    """Production store. Imports the neo4j driver lazily so the in-memory path needs no dep."""

    def __init__(self, url: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(url, auth=(user, password))
        with self._driver.session() as session:
            session.run(
                "CREATE CONSTRAINT entity_id IF NOT EXISTS "
                "FOR (n:Entity) REQUIRE n.id IS UNIQUE"
            )

    def upsert_node(self, node: Node) -> Node:
        with self._driver.session() as session:
            session.run(
                "MERGE (n:Entity {id: $id}) SET n.type = $type, n += $props",
                id=node.id,
                type=node.type,
                props=node.properties,
            )
        return node

    def upsert_edge(self, edge: Edge) -> Edge:
        rel = normalize_rel(edge.relationship)
        with self._driver.session() as session:
            session.run(
                f"MERGE (a:Entity {{id: $src}}) "
                f"MERGE (b:Entity {{id: $dst}}) "
                f"MERGE (a)-[r:`{rel}`]->(b) SET r += $props",
                src=edge.source,
                dst=edge.target,
                props=edge.properties,
            )
        return Edge(edge.source, rel, edge.target, edge.properties)

    def _to_node(self, record_node) -> Node:
        props = dict(record_node)
        node_id = props.pop("id")
        node_type = props.pop("type", "unknown")
        return Node(node_id, node_type, props)

    def get_node(self, node_id: str) -> Node | None:
        with self._driver.session() as session:
            record = session.run(
                "MATCH (n:Entity {id: $id}) RETURN n", id=node_id
            ).single()
            return self._to_node(record["n"]) if record else None

    def neighbors(
        self, node_id: str, direction: Direction = "both", relationship: str | None = None
    ) -> list[Neighbor]:
        arrow = {"out": "-[r]->", "in": "<-[r]-", "both": "-[r]-"}[direction]
        where = "WHERE type(r) = $rel" if relationship else ""
        query = (
            f"MATCH (n:Entity {{id: $id}}){arrow}(m:Entity) {where} "
            "RETURN type(r) AS rel, m AS node, "
            "startNode(r).id = $id AS is_out"
        )
        params: dict[str, object] = {"id": node_id}
        if relationship:
            params["rel"] = normalize_rel(relationship)
        with self._driver.session() as session:
            result = session.run(query, **params)
            return [
                Neighbor(
                    record["rel"],
                    "out" if record["is_out"] else "in",
                    self._to_node(record["node"]),
                )
                for record in result
            ]

    def stats(self) -> dict[str, int]:
        with self._driver.session() as session:
            nodes = session.run("MATCH (n:Entity) RETURN count(n) AS c").single()["c"]
            edges = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            return {"nodes": nodes, "edges": edges}
