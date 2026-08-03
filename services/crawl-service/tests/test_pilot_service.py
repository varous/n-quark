from datetime import UTC, datetime

from _stubs import StubGraphReader
from sqlalchemy import select

from crawl_service.config import Settings
from crawl_service.db import SessionLocal
from crawl_service.enrichment.pilot.decision import DISABLE_LOW_VALUE
from crawl_service.enrichment.pilot.reports import source_value_report, venue_coverage_report
from crawl_service.enrichment.pilot.service import FetchResult, PilotService
from crawl_service.enrichment.service import EnrichmentService
from crawl_service.models import EnrichmentRun, TrackedEvent

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

SHARE_HTML = ('<html><head><meta property="og:title" content="Free Folk Nite">'
              '<meta property="og:description" content="Aug 01, 2026, 8:00 PM Skinny Mos"></head></html>')

NODE = {"id": "event:ffn", "type": "event", "properties": {
    "display_name": "Free Folk Nite", "starts_at": "2026-08-01T20:00:00+00:00",
    "city": "Kolkata", "source_url": "https://www.boshow.in/api/shows/share/free-folk-nite"}}
NEIGHBORS = [
    {"relationship": "OCCURS_AT", "node": {"id": "venue:skinny-mos", "properties": {"display_name": "Skinny Mos"}}},
    {"relationship": "IN_REGION", "node": {"id": "region:west-bengal", "properties": {"display_name": "WB"}}}]


def _track(canonical="event:ffn", sid="free-folk-nite"):
    with SessionLocal() as s, s.begin():
        s.add(TrackedEvent(
            id="t1", source="boshow", source_record_id=sid, canonical_event_id=canonical,
            tracking_status="ACTIVE", first_tracked_at=NOW, priority=10, priority_reason={},
            created_at=NOW, updated_at=NOW))


def _pilot(fetch, min_conf=0.5):
    cfg = Settings(capture_enrichment_min_confidence=min_conf, capture_enrichment_pilot_max_events=10)
    gr = StubGraphReader(NODE, NEIGHBORS)
    enr = EnrichmentService(SessionLocal, gr, None, cfg)
    return PilotService(SessionLocal, gr, enr, cfg, page_fetch=fetch)


async def _ok_fetch(url):
    return FetchResult(200, "text/html", SHARE_HTML, 12, len(SHARE_HTML.encode()))


async def test_pilot_measures_duplicates_and_recommends_disable():
    _track()
    result = await _pilot(_ok_fetch).run(trace=True)
    m = result["metrics"]
    assert m["pages_attempted"] == 1 and m["valid_event_pages"] == 1
    assert m["duplicate_evidence_count"] == 2 and m["incremental_field_gain_count"] == 0
    assert m["conflict_count"] == 0
    assert result["recommendation"]["recommendation"] == DISABLE_LOW_VALUE
    # run persisted + auditable
    with SessionLocal() as s:
        runs = s.execute(select(EnrichmentRun)).scalars().all()
    assert len(runs) == 1 and runs[0].pages_retrieved == 1


async def test_pilot_source_value_and_venue_reports():
    _track()
    await _pilot(_ok_fetch).run()
    sv = source_value_report(SessionLocal)
    assert sv["valid_event_pages"] == 1 and sv["open_graph_presence_rate"] == 1.0
    assert sv["duplicate_evidence_count"] == 2
    vc = venue_coverage_report(SessionLocal)
    assert vc["events_with_source_venue_text"] == 1  # venue_name candidate stored from the page


async def test_pilot_handles_blocked_page():
    _track()
    async def blocked(url):
        return FetchResult(200, "text/html", "<html>Just a moment... cloudflare</html>", 5, 40)
    result = await _pilot(blocked).run(trace=True)
    assert result["metrics"]["valid_event_pages"] == 0
    assert result["metrics"]["challenge_or_block_count"] == 1
    assert result["events"][0]["suppressed"] == "BLOCKED_OR_CHALLENGE"


async def test_pilot_handles_timeout_without_candidates():
    _track()
    async def timeout(url):
        return FetchResult(None, None, "", 15000, 0)
    result = await _pilot(timeout).run()
    assert result["metrics"]["valid_event_pages"] == 0
    assert result["metrics"]["retrieval_outcomes"].get("TIMEOUT") == 1


async def test_pilot_deterministic_cohort_seed():
    for i in range(5):
        with SessionLocal() as s, s.begin():
            s.add(TrackedEvent(id=f"t{i}", source="boshow", source_record_id=f"ev{i}",
                               canonical_event_id=f"event:{i}", tracking_status="ACTIVE",
                               first_tracked_at=NOW, priority=1, priority_reason={},
                               created_at=NOW, updated_at=NOW))
    p = _pilot(_ok_fetch)
    a = [te.id for te in p.select_cohort(NOW)]
    b = [te.id for te in p.select_cohort(NOW)]
    assert a == b  # deterministic for a fixed seed
