"""Entity resolution <-> scheduler integration: a successful capture triggers best-effort entity
resolution; a failure never fails capture; disabled mode is a no-op; shared entities never create a
duplicate-event match."""

from datetime import UTC, datetime

from _stubs import MultiStubGraphReader, StubCapturer, StubGraphReader, StubGraphWriter
from sqlalchemy import select

from crawl_service.classification import SUCCESS_RECORD_PRESENT, CaptureOutcome
from crawl_service.config import Settings
from crawl_service.db import SessionLocal
from crawl_service.entity_resolution.service import EntityResolutionService
from crawl_service.models import EntityResolutionCandidate, EventMatchCandidate
from crawl_service.reconciliation.service import ReconciliationService
from crawl_service.service import SchedulerService

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _node(cid, title, city, performers, venue=None):
    node = {"id": cid, "type": "event", "properties": {"display_name": title, "city": city}}
    neighbors = [{"relationship": "FEATURES", "node": {"id": f"artist:{p}", "properties": {"display_name": p}}}
                 for p in performers]
    if venue:
        neighbors.append({"relationship": "OCCURS_AT",
                          "node": {"id": f"venue:{venue}", "properties": {"display_name": venue}}})
    return node, neighbors


def _present(sid):
    return CaptureOutcome(SUCCESS_RECORD_PRESENT, http_status=200,
                          shadow_result={"noop": True, "persisted": False, "transitions": []},
                          canonical_event_id=f"event:{sid}")


def _scheduler(reader, writer=None, **cfg_kw):
    cfg = Settings(scheduled_capture_enabled=True, entity_resolution_enabled=True,
                   entity_resolution_sources="boshow", **cfg_kw)
    er = EntityResolutionService(SessionLocal, reader, writer or StubGraphWriter(), cfg)
    return SchedulerService(SessionLocal, StubCapturer(default=_present("ev1")), cfg,
                            entity_resolver=er), cfg


async def test_capture_triggers_entity_resolution() -> None:
    reader = StubGraphReader(*_node("event:ev1", "Prateek Kuhad Live", "Mumbai", ["Prateek Kuhad"], "Antisocial"))
    svc, _ = _scheduler(reader)
    svc.enroll("boshow", "ev1", now=NOW)
    created = svc.generate_due_jobs(NOW)
    trace = await svc.process_job(created[0], NOW)
    assert trace["entity_resolution"]["outcome"] in ("ENTITY_RESOLUTION_SUCCEEDED", "ENTITY_RESOLUTION_PARTIAL")
    with SessionLocal() as s:
        cands = s.execute(select(EntityResolutionCandidate)).scalars().all()
    assert {c.entity_type for c in cands} >= {"ARTIST", "VENUE"}


async def test_entity_resolution_disabled_is_noop() -> None:
    reader = StubGraphReader(*_node("event:ev1", "X", "Mumbai", ["Someone"]))
    cfg = Settings(scheduled_capture_enabled=True, entity_resolution_enabled=False)
    er = EntityResolutionService(SessionLocal, reader, StubGraphWriter(), cfg)
    svc = SchedulerService(SessionLocal, StubCapturer(default=_present("ev1")), cfg, entity_resolver=er)
    svc.enroll("boshow", "ev1", now=NOW)
    created = svc.generate_due_jobs(NOW)
    trace = await svc.process_job(created[0], NOW)
    assert "entity_resolution" not in trace
    with SessionLocal() as s:
        assert s.execute(select(EntityResolutionCandidate)).first() is None


async def test_entity_resolution_failure_does_not_fail_capture() -> None:
    class Boom:
        async def get_event(self, event_id):
            raise RuntimeError("graph down")

    cfg = Settings(scheduled_capture_enabled=True, entity_resolution_enabled=True,
                   entity_resolution_sources="boshow")
    er = EntityResolutionService(SessionLocal, Boom(), StubGraphWriter(), cfg)
    svc = SchedulerService(SessionLocal, StubCapturer(default=_present("ev1")), cfg, entity_resolver=er)
    svc.enroll("boshow", "ev1", now=NOW)
    created = svc.generate_due_jobs(NOW)
    trace = await svc.process_job(created[0], NOW)
    assert trace["result_code"] == SUCCESS_RECORD_PRESENT and trace["job_status"] == "SUCCEEDED"
    assert trace["entity_resolution"]["outcome"] == "ENTITY_RESOLUTION_FAILED"


async def test_shared_artist_does_not_create_duplicate_event_match() -> None:
    # Two DIFFERENT events (different cities/titles) that share one artist must NOT reconcile as a
    # duplicate event: shared entities never imply a duplicate event.
    b = _node("event:b", "Prateek Kuhad", "Kolkata", ["Prateek Kuhad"], "Skinny Mos")
    d = _node("event:d", "Prateek Kuhad Live Tour", "Mumbai", ["Prateek Kuhad"], "Antisocial")
    reader = MultiStubGraphReader(mapping={"event:b": b, "event:d": d})
    cfg = Settings(entity_resolution_enabled=True, reconciliation_enabled=True,
                   second_source_capture_enabled=True, second_source_name="district")
    er = EntityResolutionService(SessionLocal, reader, StubGraphWriter(), cfg)
    await er.resolve_event(canonical_event_id="event:b", source="boshow", source_record_id="b1", now=NOW)
    await er.resolve_event(canonical_event_id="event:d", source="district", source_record_id="d1", now=NOW)

    # entity convergence happened (same canonical artist)...
    assert er.cross_inventory(entity_type="ARTIST")["count"] == 1

    # ...but reconciliation of the two events still finds no duplicate-event match (different city).
    from crawl_service.enrichment.service import EnrichmentService
    enr = EnrichmentService(SessionLocal, reader, None, cfg)
    recon = ReconciliationService(SessionLocal, reader, enr, cfg)
    # seed tracked events for both sides
    SchedulerService(SessionLocal, StubCapturer(), cfg).enroll("boshow", "b1", canonical_event_id="event:b", now=NOW)
    with SessionLocal() as s, s.begin():
        from crawl_service.models import TrackedEvent
        s.add(TrackedEvent(id="t2", source="district", source_record_id="d1",
                           canonical_event_id="event:d", tracking_status="ACTIVE",
                           first_tracked_at=NOW, priority=0, priority_reason={}, created_at=NOW, updated_at=NOW))
    res = await recon.run(left_source="boshow", right_source="district", now=NOW)
    assert res["metrics"]["auto_match"] == 0
    with SessionLocal() as s:
        matched = s.execute(select(EventMatchCandidate).where(
            EventMatchCandidate.match_status == "MATCHED")).first()
    assert matched is None
