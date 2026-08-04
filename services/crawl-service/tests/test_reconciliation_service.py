from datetime import UTC, datetime

from _stubs import MultiStubGraphReader
from sqlalchemy import select

from crawl_service.config import Settings
from crawl_service.db import SessionLocal
from crawl_service.enrichment.service import EnrichmentService
from crawl_service.models import EnrichmentCandidate, EventMatchCandidate, TrackedEvent
from crawl_service.reconciliation.service import ReconciliationService

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _node(cid, name, city, venue_id, venue_name, region_id, performers, starts="2026-09-10T19:00:00+00:00"):
    return (
        {"id": cid, "type": "event", "properties": {"display_name": name, "starts_at": starts, "city": city}},
        [{"relationship": "OCCURS_AT", "node": {"id": venue_id, "properties": {"display_name": venue_name}}},
         {"relationship": "IN_REGION", "node": {"id": region_id, "properties": {"display_name": region_id}}},
         *[{"relationship": "FEATURES", "node": {"id": f"artist:{p}", "properties": {"display_name": p}}}
           for p in performers]],
    )


def _track(source, sid, cid):
    with SessionLocal() as s, s.begin():
        s.add(TrackedEvent(id=f"{source}-{sid}", source=source, source_record_id=sid,
                           canonical_event_id=cid, tracking_status="ACTIVE", first_tracked_at=NOW,
                           priority=1, priority_reason={}, created_at=NOW, updated_at=NOW))


def _cfg():
    return Settings(reconciliation_enabled=True, second_source_name="district",
                    reconciliation_auto_match_threshold=0.75, reconciliation_possible_match_threshold=0.5)


def _svc():
    mapping = {
        "event:pk-boshow": _node("event:pk-boshow", "Prateek Kuhad Live", "Mumbai",
                                 "venue:phoenix", "Phoenix Marketcity", "region:mh", ["Prateek Kuhad"]),
        "event:pk-district": _node("event:pk-district", "Prateek Kuhad", "Mumbai",
                                   "venue:phoenix", "Phoenix Marketcity", "region:mh", ["Prateek Kuhad"]),
    }
    gr = MultiStubGraphReader(mapping)
    enr = EnrichmentService(SessionLocal, gr, None, _cfg())
    return ReconciliationService(SessionLocal, gr, enr, _cfg())


async def test_run_generates_auto_match_for_overlapping_event():
    _track("boshow", "pk-boshow", "event:pk-boshow")
    _track("district", "pk-district", "event:pk-district")
    result = await _svc().run(trace=True)
    assert result["metrics"]["auto_match"] == 1
    with SessionLocal() as s:
        m = s.execute(select(EventMatchCandidate)).scalar_one()
    assert m.match_status == "MATCHED" and m.left_canonical_event_id and m.right_canonical_event_id


async def test_different_events_do_not_match():
    _track("boshow", "pk-boshow", "event:pk-boshow")
    mapping = _svc()  # noqa: F841 — build tracked below with a non-overlapping district event
    with SessionLocal() as s, s.begin():
        s.add(TrackedEvent(id="d2", source="district", source_record_id="other",
                           canonical_event_id="event:other", tracking_status="ACTIVE",
                           first_tracked_at=NOW, priority=1, priority_reason={}, created_at=NOW, updated_at=NOW))
    gr = MultiStubGraphReader({
        "event:pk-boshow": _node("event:pk-boshow", "Prateek Kuhad Live", "Mumbai", "venue:phoenix",
                                 "Phoenix Marketcity", "region:mh", ["Prateek Kuhad"]),
        "event:other": _node("event:other", "Comedy Gala", "Delhi", "venue:x", "Some Club",
                             "region:dl", ["Random Comic"]),
    })
    svc = ReconciliationService(SessionLocal, gr, EnrichmentService(SessionLocal, gr, None, _cfg()), _cfg())
    result = await svc.run()
    # different city -> blocked out entirely (no candidate persisted)
    with SessionLocal() as s:
        assert s.execute(select(EventMatchCandidate)).scalars().all() == []
    assert result["metrics"]["auto_match"] == 0


def _cand(cid, source, field, value, source_type="SOURCE_API"):
    with SessionLocal() as s, s.begin():
        s.add(EnrichmentCandidate(
            id=f"{source}-{field}", canonical_event_id=cid, source=source, source_record_id="x",
            field_name=field, candidate_value=value, normalized_value=value, source_type=source_type,
            surface="api", source_family=source, independence_group=f"{source}_origin",
            extraction_method="DIRECT_FIELD", epistemic_status="observed_public_state",
            observed_at=NOW, confidence=0.9, content_hash=f"{source}{field}{value}",
            candidate_status="ACTIVE", created_at=NOW))


async def test_reconcile_pair_independent_consensus_and_fill():
    # both sources agree on starts_at (independent) -> consensus; only district has end_at -> fill
    _cand("event:pk-boshow", "boshow", "starts_at", "2026-09-10T19:00:00")
    _cand("event:pk-district", "district", "starts_at", "2026-09-10T19:00:00")
    _cand("event:pk-district", "district", "end_at", "2026-09-10T22:00:00")
    out = await _svc().reconcile_pair("event:pk-boshow", "event:pk-district")
    assert out["fields"]["starts_at"]["reason"] == "RESOLVED_CONSENSUS"
    assert set(out["fields"]["starts_at"]["independence_groups"]) == {"boshow_origin", "district_origin"}
    assert out["fields"]["end_at"]["right_only"] is True  # one source fills a field the other lacks
    assert out["summary"]["consensus"] >= 1 and out["summary"]["single_source_fill"] >= 1


async def test_source_price_availability_retained_separately():
    gr = MultiStubGraphReader({
        "event:pk-boshow": ({"id": "event:pk-boshow", "properties": {"price_min": 599, "currency": "INR", "availability": "AVAILABLE"}}, []),
        "event:pk-district": ({"id": "event:pk-district", "properties": {"price_min": 1499, "currency": "INR", "availability": "FEW_LEFT"}}, []),
    })
    svc = ReconciliationService(SessionLocal, gr, EnrichmentService(SessionLocal, gr, None, _cfg()), _cfg())
    out = await svc.source_price_availability("event:pk-boshow", "event:pk-district")
    assert out["source_price_comparison"]["classification"] == "PLATFORM_DIFFERENCE"
    assert out["source_price_comparison"]["left"]["price_min"] == 599
    assert out["source_price_comparison"]["right"]["price_min"] == 1499


async def test_source_records_linkage_preserves_both():
    _track("boshow", "pk-boshow", "event:pk-boshow")
    _track("district", "pk-district", "event:pk-district")
    await _svc().run()
    recs = _svc().source_records("event:pk-boshow")
    others = [r for r in recs["represented_by"] if not r["self"]]
    assert any(r["canonical_event_id"] == "event:pk-district" for r in others)
