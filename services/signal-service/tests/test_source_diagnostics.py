"""Validated discovery + per-source quality metrics (stubbed adapter, no network)."""

from datetime import UTC, datetime

import pytest

from signal_service.adapters import contract, sources
from signal_service.adapters.ticketing import EventNotFound, TicketingEvent

NOW = datetime(2026, 8, 5, tzinfo=UTC)


def _event(ref, city, **over) -> TicketingEvent:
    base = dict(  # noqa: C408
                source="skillbox", source_event_id=ref, event_slug=ref, event_name=f"{city} Night",
                event_url=f"https://s/events/{ref}", city=city, region=city, country="India",
                venue_name="Some Hall", artists=["A"], category="Music", language="", currency="INR",
                price_min=499.0, is_free=False, starts_at=datetime(2026, 9, 1, 20, tzinfo=UTC),
                capacity=None, tickets_sold=None, verified=True, image_url="https://cdn/p.jpg")
    base.update(over)
    return TicketingEvent(**base)


class _StubAdapter:
    source = "skillbox"

    def __init__(self, events):
        self._events = events

    async def discover(self, *, city=None, limit=20):
        return list(self._events)

    async def fetch_event(self, ref):
        v = self._events[ref]
        if isinstance(v, Exception):
            raise v
        return v

    def classify_failure(self, exc):
        return contract.classify_failure(exc)


@pytest.fixture()
def stub(monkeypatch):
    events = {
        "kol-good": _event("kol-good", "Kolkata"),
        "kol-good2": _event("kol-good2", "Kolkata"),
        "mum-good": _event("mum-good", "Mumbai"),                       # accepted but wrong city
        "placeholder": _event("placeholder", "Mutiple Cities, India"),  # rejected
        "gone": EventNotFound("skillbox: absent"),                      # fetch → record absent
    }
    monkeypatch.setattr(sources, "get_adapter", lambda s: _StubAdapter(events))
    return events


async def test_validated_discovery_partitions_records(stub):
    r = await sources.validated_discovery("skillbox", city="Kolkata", limit=10, fetch_cap=10, now=NOW)
    assert {a.event_ref for a in r.accepted} == {"kol-good", "kol-good2"}
    assert {o.event_ref for o in r.out_of_scope} == {"mum-good"}         # right quality, wrong city
    rejected_refs = {x.event_ref for x in r.rejected}
    assert {"placeholder", "gone"} <= rejected_refs
    assert r.candidates_considered == 5


async def test_quality_report_shape(stub):
    r = await sources.validated_discovery("skillbox", city="Kolkata", limit=10, fetch_cap=10, now=NOW)
    q = sources.quality_report(r)
    assert q["records_accepted"] == 2 and q["records_out_of_scope"] == 1
    assert q["rejections_by_reason"].get("MULTIPLE_CITIES_PLACEHOLDER") == 1
    assert q["rejections_by_reason"].get(contract.SUCCESS_RECORD_ABSENT) == 1
    # field quality present/valid/specific tracked (city present for all fetched)
    assert q["field_quality"]["city"]["present"] == 1.0


async def test_source_unavailable_is_graceful(monkeypatch):
    class Boom:
        source = "skillbox"

        async def discover(self, *, city=None, limit=20):
            raise RuntimeError("sitemap down")

    monkeypatch.setattr(sources, "get_adapter", lambda s: Boom())
    r = await sources.validated_discovery("skillbox", city="Kolkata", now=NOW)
    assert r.available is False and "sitemap down" in r.error
