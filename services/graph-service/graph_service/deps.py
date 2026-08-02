"""Store selection. Routes depend on get_store; tests override it with an in-memory store."""

from graph_service.config import settings
from graph_service.store import (
    GraphStore,
    InMemoryGraphStore,
    Neo4jGraphStore,
    PostgresGraphStore,
)

_store: GraphStore | None = None
_shadow_store = None


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


def get_shadow_store():
    """Shadow Ledger store (Phase 1). Always Postgres-backed (SQLite in tests via override)."""
    global _shadow_store
    if _shadow_store is None:
        from graph_service.shadow_store import ShadowStore

        _shadow_store = ShadowStore(settings.postgres_url)
    return _shadow_store
