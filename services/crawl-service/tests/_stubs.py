"""Importable test helpers (conftest sets the SQLite env before this imports crawl_service)."""

from dataclasses import dataclass, field
from typing import Any

from crawl_service.classification import SUCCESS_RECORD_PRESENT, CaptureOutcome


@dataclass
class StubCapturer:
    """Deterministic capturer for tests. Enqueue `outcomes` or set a `default`."""

    outcomes: list[CaptureOutcome] = field(default_factory=list)
    default: CaptureOutcome | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def capture(self, *, source, source_record_id, canonical_event_id):
        self.calls.append(
            {"source": source, "source_record_id": source_record_id,
             "canonical_event_id": canonical_event_id}
        )
        if self.outcomes:
            return self.outcomes.pop(0)
        if self.default is not None:
            return self.default
        return CaptureOutcome(
            SUCCESS_RECORD_PRESENT, http_status=200,
            shadow_result={"noop": True, "persisted": False, "transitions": []},
            canonical_event_id=f"event:{source_record_id}",
        )


@dataclass
class StubGraphReader:
    """Returns a fixed canonical event node + neighbors for enrichment tests."""

    node: dict | None = None
    neighbors: list = field(default_factory=list)

    async def get_event(self, event_id: str):
        return self.node, self.neighbors


@dataclass
class MultiStubGraphReader:
    """Returns a per-event (node, neighbors) from a mapping keyed by canonical_event_id."""

    mapping: dict = field(default_factory=dict)  # event_id -> (node, neighbors)
    nodes_by_type: dict = field(default_factory=dict)  # node_type -> [node dicts] (5A.3.2 reconcile)

    async def get_event(self, event_id: str):
        return self.mapping.get(event_id, (None, []))

    async def list_nodes(self, *, node_type: str, limit: int = 500):
        return list(self.nodes_by_type.get(node_type, []))[:limit]


@dataclass
class StubGraphWriter:
    """Captures graph batch upserts so entity-resolution graph writes can be asserted offline."""

    batches: list[dict] = field(default_factory=list)

    async def upsert_batch(self, nodes: list[dict], edges: list[dict]) -> dict:
        self.batches.append({"nodes": nodes, "edges": edges})
        return {"nodes": len(nodes), "edges": len(edges)}

    def edges(self) -> list[dict]:
        return [e for b in self.batches for e in b["edges"]]

    def nodes(self) -> list[dict]:
        return [n for b in self.batches for n in b["nodes"]]


@dataclass
class StubPageFetcher:
    """Returns fixed (html, visible_text) for public-page enrichment tests; None simulates failure."""

    html: str = ""
    text: str = ""
    fail: bool = False

    async def fetch(self, url: str):
        return None if self.fail else (self.html, self.text)
