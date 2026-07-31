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
from datetime import UTC, datetime
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
    def list_nodes(self, node_type: str | None = None, limit: int = 100) -> list[Node]: ...
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
        now = datetime.now(UTC).isoformat()  # store-managed last-write, for incremental sync
        existing = self._nodes.get(node.id)
        if existing is not None:
            if node.type and node.type != "unknown":
                existing.type = node.type
            existing.properties.update(node.properties)
            existing.properties["updated_at"] = now
            return existing
        created = Node(node.id, node.type or "unknown", {**node.properties, "updated_at": now})
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

    def list_nodes(self, node_type: str | None = None, limit: int = 100) -> list[Node]:
        nodes = [n for n in self._nodes.values() if node_type is None or n.type == node_type]
        return nodes[:limit]

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


class PostgresGraphStore:
    """Postgres-backed graph store — the consolidated backend (one datastore for relational,
    graph, and — later — pgvector).

    The graph workload is shallow (neighbours, list-by-type, feed assembly), so an adjacency
    model in two tables covers it without a graph-native engine. Semantics mirror the in-memory
    store exactly: ``updated_at`` lands inside node properties for the feed's incremental cursor,
    edges MERGE on the (source, relationship, target) triple, and edge endpoints are auto-created
    as thin nodes. Writes are read-modify-write in a transaction — portable across Postgres and
    the SQLite used by unit tests, and fine for this catalog's write volume.
    """

    def __init__(self, url: str) -> None:
        from sqlalchemy import create_engine, func, select
        from sqlalchemy.orm import sessionmaker

        from graph_service.models import Base, GraphEdgeRecord, GraphNodeRecord

        self._func = func
        self._select = select
        self._NodeRow = GraphNodeRecord
        self._EdgeRow = GraphEdgeRecord

        connect_args: dict[str, object] = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        self._engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
        # Alembic owns the prod schema (run at boot); create_all is an idempotent, check-first
        # fallback so offline dev and the SQLite test path work without a migration step.
        Base.metadata.create_all(self._engine)
        self._Session = sessionmaker(bind=self._engine, autoflush=False, expire_on_commit=False)

    def upsert_node(self, node: Node) -> Node:
        now = datetime.now(UTC)
        now_iso = now.isoformat()  # store-managed last-write, for incremental sync
        with self._Session() as session, session.begin():
            row = session.get(self._NodeRow, node.id)
            if row is None:
                props = {**node.properties, "updated_at": now_iso}
                session.add(
                    self._NodeRow(
                        id=node.id, type=node.type or "unknown", properties=props, updated_at=now
                    )
                )
                return Node(node.id, node.type or "unknown", props)
            if node.type and node.type != "unknown":
                row.type = node.type
            merged = {**row.properties, **node.properties, "updated_at": now_iso}
            row.properties = merged  # reassign: JSON columns aren't mutation-tracked
            row.updated_at = now
            return Node(row.id, row.type, merged)

    def _ensure_node(self, session, node_id: str) -> None:
        if session.get(self._NodeRow, node_id) is None:
            session.add(
                self._NodeRow(
                    id=node_id, type="unknown", properties={}, updated_at=datetime.now(UTC)
                )
            )

    def upsert_edge(self, edge: Edge) -> Edge:
        rel = normalize_rel(edge.relationship)
        with self._Session() as session, session.begin():
            self._ensure_node(session, edge.source)
            self._ensure_node(session, edge.target)
            row = session.get(self._EdgeRow, (edge.source, rel, edge.target))
            if row is None:
                session.add(
                    self._EdgeRow(
                        source=edge.source, relationship=rel, target=edge.target,
                        properties=dict(edge.properties),
                    )
                )
                return Edge(edge.source, rel, edge.target, dict(edge.properties))
            merged = {**row.properties, **edge.properties}
            row.properties = merged
            return Edge(edge.source, rel, edge.target, merged)

    def _to_node(self, row) -> Node:
        return Node(row.id, row.type, dict(row.properties))

    def get_node(self, node_id: str) -> Node | None:
        with self._Session() as session:
            row = session.get(self._NodeRow, node_id)
            return self._to_node(row) if row else None

    def list_nodes(self, node_type: str | None = None, limit: int = 100) -> list[Node]:
        with self._Session() as session:
            stmt = self._select(self._NodeRow)
            if node_type is not None:
                stmt = stmt.where(self._NodeRow.type == node_type)
            stmt = stmt.order_by(self._NodeRow.id).limit(limit)
            return [self._to_node(row) for row in session.scalars(stmt)]

    def neighbors(
        self, node_id: str, direction: Direction = "both", relationship: str | None = None
    ) -> list[Neighbor]:
        rel = normalize_rel(relationship) if relationship else None
        out: list[Neighbor] = []
        with self._Session() as session:
            if direction in ("out", "both"):
                stmt = self._select(self._EdgeRow).where(self._EdgeRow.source == node_id)
                if rel is not None:
                    stmt = stmt.where(self._EdgeRow.relationship == rel)
                for edge in session.scalars(stmt):
                    target = session.get(self._NodeRow, edge.target)
                    if target is not None:
                        out.append(Neighbor(edge.relationship, "out", self._to_node(target)))
            if direction in ("in", "both"):
                stmt = self._select(self._EdgeRow).where(self._EdgeRow.target == node_id)
                if rel is not None:
                    stmt = stmt.where(self._EdgeRow.relationship == rel)
                for edge in session.scalars(stmt):
                    source = session.get(self._NodeRow, edge.source)
                    if source is not None:
                        out.append(Neighbor(edge.relationship, "in", self._to_node(source)))
        return out

    def stats(self) -> dict[str, int]:
        with self._Session() as session:
            nodes = session.scalar(self._select(self._func.count()).select_from(self._NodeRow))
            edges = session.scalar(self._select(self._func.count()).select_from(self._EdgeRow))
            return {"nodes": nodes or 0, "edges": edges or 0}


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
        now = datetime.now(UTC).isoformat()  # store-managed last-write, for incremental sync
        with self._driver.session() as session:
            session.run(
                "MERGE (n:Entity {id: $id}) SET n.type = $type, n += $props, n.updated_at = $now",
                id=node.id,
                type=node.type,
                props=node.properties,
                now=now,
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

    def list_nodes(self, node_type: str | None = None, limit: int = 100) -> list[Node]:
        with self._driver.session() as session:
            if node_type:
                query = "MATCH (n:Entity {type: $type}) RETURN n ORDER BY n.id LIMIT $lim"
                params: dict[str, object] = {"type": node_type, "lim": limit}
            else:
                query = "MATCH (n:Entity) RETURN n ORDER BY n.id LIMIT $lim"
                params = {"lim": limit}
            return [self._to_node(record["n"]) for record in session.run(query, **params)]

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
