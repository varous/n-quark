from datetime import UTC, datetime

import pytest
from _stubs import MultiStubGraphReader, StubGraphWriter

from crawl_service.config import Settings
from crawl_service.entity_resolution.service import (
    ER_NO_EVIDENCE,
    ER_SUCCEEDED,
    EntityResolutionService,
)

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _event(cid, *, title, city=None, organizer=None, venue=None, performers=(), region=None):
    node = {"id": cid, "type": "event",
            "properties": {"display_name": title, "city": city, "organizer": organizer,
                           "source_url": f"https://x/{cid}"}}
    neighbors = []
    if venue:
        neighbors.append({"relationship": "OCCURS_AT",
                          "node": {"id": f"venue:{venue}", "properties": {"display_name": venue}}})
    for p in performers:
        neighbors.append({"relationship": "FEATURES",
                          "node": {"id": f"artist:{p}", "properties": {"display_name": p}}})
    if region:
        neighbors.append({"relationship": "IN_REGION",
                          "node": {"id": region, "properties": {"display_name": region}}})
    return node, neighbors


def _svc(session_factory, mapping, writer=None, cfg=None):
    return EntityResolutionService(session_factory, MultiStubGraphReader(mapping=mapping),
                                   writer or StubGraphWriter(), cfg or Settings())


@pytest.mark.asyncio
async def test_resolve_event_persists_and_writes_graph(session_factory):
    mapping = {"event:pk": _event("event:pk", title="Prateek Kuhad Live", city="Mumbai",
                                   organizer="OML", venue="Phoenix Marketcity",
                                   performers=["Prateek Kuhad"], region="region:maharashtra")}
    writer = StubGraphWriter()
    svc = _svc(session_factory, mapping, writer)
    res = await svc.resolve_event(canonical_event_id="event:pk", source="boshow", source_record_id="pk-1", now=NOW)
    assert res["outcome"] == ER_SUCCEEDED
    rels = {e["relationship"] for e in writer.edges()}
    assert {"FEATURES", "OCCURS_AT", "ORGANIZED_BY", "IDENTIFIES"} <= rels
    # resolved entities recorded against the event
    resolved = svc.resolved_entities("event:pk")
    types = {e["entity_type"] for e in resolved["entities"]}
    assert {"ARTIST", "VENUE", "ORGANIZER"} <= types


@pytest.mark.asyncio
async def test_shared_artist_across_sources_converges_one_canonical(session_factory):
    mapping = {
        "event:b": _event("event:b", title="Prateek Kuhad", city="Kolkata", performers=["Prateek Kuhad"]),
        "event:d": _event("event:d", title="Prateek Kuhad Live", city="Mumbai", performers=["Prateek Kuhad"]),
    }
    svc = _svc(session_factory, mapping)
    await svc.resolve_event(canonical_event_id="event:b", source="boshow", source_record_id="b1", now=NOW)
    await svc.resolve_event(canonical_event_id="event:d", source="district", source_record_id="d1", now=NOW)
    xi = svc.cross_inventory(entity_type="ARTIST")
    assert xi["count"] == 1
    row = xi["cross_source_entities"][0]
    assert set(row["by_source"]) == {"boshow", "district"} and sorted(row["cities"]) == ["Kolkata", "Mumbai"]


@pytest.mark.asyncio
async def test_source_handles_link_multiple_sources(session_factory):
    mapping = {
        "event:b": _event("event:b", title="X", city="Kolkata", performers=["Prateek Kuhad"]),
        "event:d": _event("event:d", title="Y", city="Mumbai", performers=["Prateek Kuhad"]),
    }
    svc = _svc(session_factory, mapping)
    r = await svc.resolve_event(canonical_event_id="event:b", source="boshow", source_record_id="b1", now=NOW)
    await svc.resolve_event(canonical_event_id="event:d", source="district", source_record_id="d1", now=NOW)
    artist_cid = next(e["canonical_entity_id"] for e in r["entities"] if e["entity_type"] == "ARTIST")
    handles = svc.source_handles("ARTIST", artist_cid)
    assert {h["source"] for h in handles["handles"]} == {"boshow", "district"}


@pytest.mark.asyncio
async def test_unresolved_venue_later_resolves_history(session_factory):
    # first capture: venue text but no city -> UNRESOLVED
    m1 = {"event:s": _event("event:s", title="Gig", performers=[], venue="Skinny Mos")}
    writer = StubGraphWriter()
    svc = _svc(session_factory, m1, writer)
    await svc.resolve_event(canonical_event_id="event:s", source="boshow", source_record_id="s1", now=NOW)
    q = svc.unresolved(entity_type="VENUE")
    assert q["count"] == 1 and q["items"][0]["status"] in ("UNRESOLVED", "POSSIBLE_MATCH")
    cand_id = q["items"][0]["id"]

    # later capture: city arrives -> RESOLVED, history records the transition
    svc._graph.mapping["event:s"] = _event("event:s", title="Gig", venue="Skinny Mos", city="Kolkata")
    await svc.resolve_event(canonical_event_id="event:s", source="boshow", source_record_id="s1",
                            now=datetime(2026, 8, 2, tzinfo=UTC))
    detail = svc.candidate(cand_id)
    assert detail["status"] == "RESOLVED"
    assert any(h["new_status"] == "RESOLVED" for h in detail["history"])


@pytest.mark.asyncio
async def test_geography_failure_modes(session_factory):
    mapping = {
        "event:novenue": _event("event:novenue", title="A", city="Delhi", performers=["Someone"]),
        "event:generic": _event("event:generic", title="B", venue="Town Hall"),
    }
    svc = _svc(session_factory, mapping)
    r1 = await svc.resolve_event(canonical_event_id="event:novenue", source="boshow", source_record_id="n1", now=NOW)
    assert r1["geography"]["status"] == "NO_VENUE_TEXT"
    r2 = await svc.resolve_event(canonical_event_id="event:generic", source="boshow", source_record_id="g1", now=NOW)
    assert r2["geography"]["status"] == "AMBIGUOUS_VENUE"


@pytest.mark.asyncio
async def test_no_evidence_event(session_factory):
    svc = _svc(session_factory, {"event:empty": (None, [])})
    res = await svc.resolve_event(canonical_event_id="event:empty", source="boshow", source_record_id="e1", now=NOW)
    assert res["outcome"] == ER_NO_EVIDENCE


@pytest.mark.asyncio
async def test_coverage_metrics(session_factory):
    mapping = {
        "event:b": _event("event:b", title="X", city="Kolkata", performers=["Prateek Kuhad"], venue="Skinny Mos"),
        "event:d": _event("event:d", title="Y", city="Mumbai", performers=["Prateek Kuhad"], venue="Antisocial"),
    }
    svc = _svc(session_factory, mapping)
    await svc.resolve_event(canonical_event_id="event:b", source="boshow", source_record_id="b1", now=NOW)
    await svc.resolve_event(canonical_event_id="event:d", source="district", source_record_id="d1", now=NOW)
    cov = svc.coverage()
    artist = cov["by_entity_type"]["ARTIST"]
    assert artist["mentions"] == 2 and artist["cross_source_canonical_entities"] == 1


@pytest.mark.asyncio
async def test_generic_organizer_not_written_to_graph(session_factory):
    mapping = {"event:g": _event("event:g", title="Show", city="Pune", organizer="Events",
                                  performers=["Real Artist"])}
    writer = StubGraphWriter()
    svc = _svc(session_factory, mapping, writer)
    await svc.resolve_event(canonical_event_id="event:g", source="boshow", source_record_id="g1", now=NOW)
    # ambiguous "Events" organizer must not get an ORGANIZED_BY edge
    assert not any(e["relationship"] == "ORGANIZED_BY" for e in writer.edges())
