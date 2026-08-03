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
