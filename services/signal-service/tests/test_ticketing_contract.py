"""Shared ticketing-adapter contract compliance (Phase 4C)."""

from datetime import UTC, datetime

import httpx
import pytest

from signal_service.adapters import contract
from signal_service.adapters.contract import TicketingAdapter, get_adapter
from signal_service.adapters.ticketing import EventNotFound, TicketingEvent


def _event(**over) -> TicketingEvent:
    base = dict(  # noqa: C408
                source="boshow", source_event_id="sid1", event_slug="free-folk-nite",
                event_name="Free Folk Nite", event_url="https://x/events/free-folk-nite",
                city="Kolkata", region="West Bengal", country="India", venue_name="Skinny Mos",
                artists=["Skinny Mos", "Pilu"], category="Music", language="English", currency="INR",
                price_min=599.0, is_free=False, starts_at=datetime(2026, 9, 1, tzinfo=UTC),
                capacity=50, tickets_sold=10, verified=True, curator="Acme Events",
                image_url="https://cdn/x.jpg")
    base.update(over)
    return TicketingEvent(**base)


@pytest.mark.parametrize("source", ["boshow", "district", "skillbox", "mock"])
def test_adapters_conform_to_contract(source):
    a = get_adapter(source)
    assert isinstance(a, TicketingAdapter)  # runtime_checkable Protocol
    assert a.source in (source, "mock")
    for cap in ("discover", "fetch_event", "normalize_event", "classify_failure",
                "extract_source_handles", "extract_asset_references"):
        assert hasattr(a, cap)


def test_source_handles_extraction():
    h = contract.extract_source_handles(_event())
    assert h["event"] == "boshow:show:sid1"
    assert h["venue"] == "boshow:venue:skinny-mos"
    assert h["organizer"] == "boshow:organizer:acme-events"
    assert h["artists"] and h["artists"][0].startswith("boshow:artist:")
    assert h["region"] == "region:west-bengal"


def test_asset_reference_extraction():
    refs = contract.extract_asset_references(_event())
    assert refs == [{"asset_url": "https://cdn/x.jpg", "asset_role": "POSTER",
                     "source_page_url": "https://x/events/free-folk-nite"}]
    assert contract.extract_asset_references(_event(image_url=None)) == []


def test_normalize_event_via_adapter():
    obs = get_adapter("boshow").normalize_event(_event())
    attrs = {o.attribute for o in obs}
    assert {"event_name", "occurs_at_venue", "lineup", "starts_at", "price_min"} <= attrs


def test_classify_failure_mapping():
    assert contract.classify_failure(EventNotFound("x")) == contract.SUCCESS_RECORD_ABSENT
    assert contract.classify_failure(httpx.TimeoutException("t")) == contract.SOURCE_UNAVAILABLE
    req = httpx.Request("GET", "https://x")
    assert contract.classify_failure(httpx.HTTPStatusError(
        "e", request=req, response=httpx.Response(429, request=req))) == contract.RATE_LIMITED
    assert contract.classify_failure(httpx.HTTPStatusError(
        "e", request=req, response=httpx.Response(403, request=req))) == contract.BLOCKED
    assert contract.classify_failure(httpx.HTTPStatusError(
        "e", request=req, response=httpx.Response(404, request=req))) == contract.SUCCESS_RECORD_ABSENT
    assert contract.classify_failure(ValueError("bad json")) == contract.MALFORMED_RESPONSE
    assert contract.classify_failure(RuntimeError("weird")) == contract.TERMINAL_RECORD_ERROR
