"""Store selection. Routes depend on get_store; tests override it with an in-memory store."""

from graph_service.config import settings
from graph_service.store import (
    GraphStore,
    InMemoryGraphStore,
    Neo4jGraphStore,
    PostgresGraphStore,
)

_store: GraphStore | None = None


def get_store() -> GraphStore:
    global _store
    if _store is None:
        if settings.graph_backend == "neo4j":
            _store = Neo4jGraphStore(
                settings.neo4j_url, settings.neo4j_user, settings.neo4j_password
            )
        elif settings.graph_backend == "postgres":
            _store = PostgresGraphStore(settings.postgres_url)
        else:
            _store = InMemoryGraphStore()
    return _store
