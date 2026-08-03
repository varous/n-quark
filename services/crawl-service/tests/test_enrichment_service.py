from datetime import UTC, datetime, timedelta

from _stubs import StubGraphReader, StubPageFetcher
from sqlalchemy import select

from crawl_service.config import Settings
from crawl_service.db import SessionLocal
from crawl_service.enrichment.service import (
    ENRICHMENT_PARTIAL,
    ENRICHMENT_SUCCEEDED,
    EnrichmentService,
)
from crawl_service.models import EnrichmentCandidate

EV = "event:e1"
T1 = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
T2 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

_GRAPH_NODE = {
    "id": EV, "type": "event",
    "properties": {"display_name": "Free Folk Nite", "starts_at": "2026-09-10T19:00:00",
                   "city": "Kolkata", "source_url": "https://boshow.in/x"},
}
_GRAPH_NEIGHBORS = [
    {"relationship": "OCCURS_AT", "node": {"id": "venue:skinny-mos", "properties": {"display_name": "Skinny Mos"}}},
    {"relationship": "IN_REGION", "node": {"id": "region:west-bengal", "properties": {"display_name": "WB"}}},
]


def _cfg(**kw):
    return Settings(capture_enrichment_enabled=True, **kw)


async def test_graph_relationship_resolves_date_and_venue_geography() -> None:
    enr = EnrichmentService(SessionLocal, StubGraphReader(_GRAPH_NODE, _GRAPH_NEIGHBORS), None, _cfg())
    res = await enr.enrich(canonical_event_id=EV, source="boshow", source_record_id="e1", now=T2)
    assert res["outcome"] in (ENRICHMENT_SUCCEEDED, ENRICHMENT_PARTIAL)
    assert res["resolved"]["starts_at"] == "2026-09-10T19:00:00"        # scheduler-relevant
    assert res["fields"]["city"]["method"] == "CANONICAL_RELATIONSHIP"  # derived, not asserted as direct
    assert res["fields"]["region_id"]["value"] == "region:west-bengal"
    assert res["fields"]["venue_id"]["value"] == "venue:skinny-mos"


async def test_candidate_storage_idempotent_and_supersede() -> None:
    enr = EnrichmentService(SessionLocal, StubGraphReader(_GRAPH_NODE, _GRAPH_NEIGHBORS), None, _cfg())
    await enr.enrich(canonical_event_id=EV, source="boshow", source_record_id="e1", now=T2)
    await enr.enrich(canonical_event_id=EV, source="boshow", source_record_id="e1", now=T2)  # identical
    with SessionLocal() as s:
        starts = s.execute(select(EnrichmentCandidate).where(
            EnrichmentCandidate.field_name == "starts_at")).scalars().all()
    assert len(starts) == 1  # identical evidence -> no duplicate candidate

    # changed graph date -> new ACTIVE candidate, old one superseded
    changed = {**_GRAPH_NODE, "properties": {**_GRAPH_NODE["properties"], "starts_at": "2026-09-11T19:00:00"}}
    enr2 = EnrichmentService(SessionLocal, StubGraphReader(changed, _GRAPH_NEIGHBORS), None, _cfg())
    await enr2.enrich(canonical_event_id=EV, source="boshow", source_record_id="e1", now=T2 + timedelta(hours=1))
    with SessionLocal() as s:
        rows = s.execute(select(EnrichmentCandidate).where(
            EnrichmentCandidate.field_name == "starts_at")).scalars().all()
    statuses = sorted(r.candidate_status for r in rows)
    assert statuses == ["ACTIVE", "SUPERSEDED"]


async def test_public_page_higher_authority_wins_over_opengraph() -> None:
    html = ('<script type="application/ld+json">'
            '{"@type":"Event","name":"Free Folk Nite","startDate":"2026-09-10T19:00:00"}</script>'
            '<meta property="og:event:start_time" content="2026-01-01T00:00:00">')
    enr = EnrichmentService(
        SessionLocal, StubGraphReader(None, []), StubPageFetcher(html=html, text=""),
        _cfg(capture_enrichment_public_page_enabled=True))
    res = await enr.enrich(canonical_event_id=EV, source="boshow", source_record_id="free-folk-nite",
                           source_url="https://boshow.in/x", now=T2)
    assert res["fields"]["starts_at"]["value"] == "2026-09-10T19:00:00"  # JSON-LD beats OG
    assert res["fields"]["starts_at"]["contradicting"] >= 1


async def test_on_sale_interval_never_invents_exact_timestamp() -> None:
    enr = EnrichmentService(SessionLocal, StubGraphReader(None, []), None, _cfg())
    await enr.enrich(canonical_event_id=EV, source="boshow", source_record_id="e1",
                     currently_on_sale=False, now=T1)   # observed not on sale
    res = await enr.enrich(canonical_event_id=EV, source="boshow", source_record_id="e1",
                           currently_on_sale=True, now=T2)  # now on sale -> bracketed interval
    assert res["fields"]["estimated_on_sale_window_start"]["value"] == T1.isoformat()
    assert res["fields"]["estimated_on_sale_window_end"]["value"] == T2.isoformat()
    assert "source_on_sale_at" not in res["fields"]  # no exact on-sale time fabricated


async def test_first_observation_already_on_sale_records_only_first_seen() -> None:
    enr = EnrichmentService(SessionLocal, StubGraphReader(None, []), None, _cfg())
    res = await enr.enrich(canonical_event_id=EV, source="boshow", source_record_id="e1",
                           currently_on_sale=True, now=T2)
    assert res["fields"]["first_ticket_state_seen_at"]["value"] == T2.isoformat()
    assert "estimated_on_sale_window_start" not in res["fields"]
    assert "source_on_sale_at" not in res["fields"]


async def test_no_evidence_leaves_fields_unresolved() -> None:
    enr = EnrichmentService(SessionLocal, StubGraphReader(None, []), None, _cfg())
    res = await enr.enrich(canonical_event_id=EV, source="boshow", source_record_id="e1",
                           currently_on_sale=True, now=T2)
    # only the temporal first-seen resolves; date/city stay absent (never guessed)
    assert "starts_at" not in res["fields"] and "city" not in res["fields"]


async def test_get_enrichment_returns_provenance() -> None:
    enr = EnrichmentService(SessionLocal, StubGraphReader(_GRAPH_NODE, _GRAPH_NEIGHBORS), None, _cfg())
    await enr.enrich(canonical_event_id=EV, source="boshow", source_record_id="e1", now=T2)
    view = enr.get_enrichment(EV)
    assert view["resolved_fields"]["starts_at"]["value"] == "2026-09-10T19:00:00"
    assert any(c["field"] == "city" and c["source_type"] == "CANONICAL_ENTITY_RELATIONSHIP"
               for c in view["candidates"])
