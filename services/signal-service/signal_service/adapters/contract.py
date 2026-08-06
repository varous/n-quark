"""Shared ticketing-adapter contract (Phase 4C).

One typed interface every ticketing source implements, so downstream services consume only the
normalized `TicketingEvent` (never source-specific shapes). This is additive: the existing providers
(`TicketingProvider`: discover/extract) are wrapped by `BaseTicketingAdapter`, which adds the rest of
the contract using the module-level normalization already proven for Boshow/District. No provider is
rewritten and no behaviour changes.

Capabilities: discover · fetch_event · normalize_event · classify_failure · extract_source_handles ·
extract_asset_references.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx

from signal_service.adapters import ticketing as tk
from signal_service.adapters.ticketing import EventNotFound, TicketingEvent
from signal_service.schemas import NormalizedObservation

# Record/fetch outcomes (aligned with the scheduler's result-code semantics).
SUCCESS_RECORD_PRESENT = "SUCCESS_RECORD_PRESENT"
SUCCESS_RECORD_ABSENT = "SUCCESS_RECORD_ABSENT"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
RATE_LIMITED = "RATE_LIMITED"
BLOCKED = "BLOCKED"
MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
TERMINAL_RECORD_ERROR = "TERMINAL_RECORD_ERROR"


@runtime_checkable
class TicketingAdapter(Protocol):
    """The typed contract for a ticketing source."""

    source: str

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]: ...

    async def fetch_event(self, event_ref: str) -> TicketingEvent: ...

    def normalize_event(self, event: TicketingEvent) -> list[NormalizedObservation]: ...

    def classify_failure(self, exc: BaseException) -> str: ...

    def extract_source_handles(self, event: TicketingEvent) -> dict[str, Any]: ...

    def extract_asset_references(self, event: TicketingEvent) -> list[dict[str, Any]]: ...


def classify_failure(exc: BaseException) -> str:
    """Map a fetch exception to a source-neutral outcome code (deterministic)."""
    if isinstance(exc, EventNotFound):
        return SUCCESS_RECORD_ABSENT
    if isinstance(exc, httpx.TimeoutException):
        return SOURCE_UNAVAILABLE
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 404:
            return SUCCESS_RECORD_ABSENT
        if code == 429:
            return RATE_LIMITED
        if code in (401, 403):
            return BLOCKED
        return SOURCE_UNAVAILABLE
    if isinstance(exc, httpx.HTTPError):
        return SOURCE_UNAVAILABLE
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return MALFORMED_RESPONSE
    return TERMINAL_RECORD_ERROR


def extract_source_handles(event: TicketingEvent) -> dict[str, Any]:
    """Type-neutral source handles this event contributes as canonical-resolution evidence.

    Handles only — resolution to canonical ids happens later (entity-service / crawl entity resolution),
    and a shared handle never implies a duplicate event.
    """
    handles: dict[str, Any] = {
        "event": tk.event_handle(event),
        "venue": tk.venue_handle(event) if event.venue_name else None,
        "organizer": (f"{event.source}:organizer:{tk._slug(event.curator)}" if event.curator else None),
        "artists": [tk.artist_handle(event, name, i) for i, name in enumerate(event.artists)],
        "region": (f"region:{tk._slug(event.region)}" if event.region else None),
        # series is only *evidence* here (the event title); the resolver decides if it's a real series.
        "event_series_evidence": event.event_name or None,
    }
    return handles


def extract_asset_references(event: TicketingEvent) -> list[dict[str, Any]]:
    """Public creative references (for media-service). The primary listing image only; no scraping of
    additional assets in this phase."""
    if not event.image_url:
        return []
    return [{"asset_url": event.image_url, "asset_role": "POSTER",
             "source_page_url": event.event_url}]


class BaseTicketingAdapter:
    """Adapts an existing `TicketingProvider` to the full `TicketingAdapter` contract."""

    def __init__(self, provider: tk.TicketingProvider) -> None:
        self._provider = provider
        self.source = provider.name

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        return await self._provider.discover(city=city, limit=limit)

    async def fetch_event(self, event_ref: str) -> TicketingEvent:
        return await self._provider.extract(event_ref)

    def normalize_event(self, event: TicketingEvent) -> list[NormalizedObservation]:
        return tk.normalize_event(event)

    def classify_failure(self, exc: BaseException) -> str:
        return classify_failure(exc)

    def extract_source_handles(self, event: TicketingEvent) -> dict[str, Any]:
        return extract_source_handles(event)

    def extract_asset_references(self, event: TicketingEvent) -> list[dict[str, Any]]:
        return extract_asset_references(event)


def get_adapter(source: str | None = None) -> BaseTicketingAdapter:
    """Return the typed adapter for a source (wraps the existing provider factory)."""
    return BaseTicketingAdapter(tk.get_provider(source))
