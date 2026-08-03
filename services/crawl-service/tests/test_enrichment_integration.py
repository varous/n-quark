"""Enrichment ↔ scheduler integration: resolved fields flow into tracked_event and recalc cadence,
without ever failing the capture."""

from datetime import UTC, datetime

from _stubs import StubCapturer, StubGraphReader
from sqlalchemy import select

from crawl_service.classification import SUCCESS_RECORD_PRESENT, CaptureOutcome
from crawl_service.config import Settings
from crawl_service.db import SessionLocal
from crawl_service.enrichment.service import EnrichmentService
from crawl_service.models import TrackedEvent
from crawl_service.service import SchedulerService

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

_NODE = {"id": "event:ev1", "type": "event",
         "properties": {"display_name": "E1", "starts_at": "2026-09-10T19:00:00",
                        "city": "Kolkata", "source_url": "https://boshow.in/x"}}
_NEIGHBORS = [
    {"relationship": "OCCURS_AT", "node": {"id": "venue:hall", "properties": {"display_name": "Hall"}}},
    {"relationship": "IN_REGION", "node": {"id": "region:west-bengal", "properties": {"display_name": "WB"}}},
]


def _present(sid):
    return CaptureOutcome(SUCCESS_RECORD_PRESENT, http_status=200,
                          shadow_result={"noop": True, "persisted": False, "transitions": []},
                          canonical_event_id=f"event:{sid}")


def _svc(graph_node=_NODE, neighbors=_NEIGHBORS, capturer=None, **cfg_kw):
    cfg = Settings(scheduled_capture_enabled=True, capture_enrichment_enabled=True, **cfg_kw)
    enr = EnrichmentService(SessionLocal, StubGraphReader(graph_node, neighbors), None, cfg)
    cap = capturer or StubCapturer(default=_present("ev1"))
    return SchedulerService(SessionLocal, cap, cfg, enricher=enr)


async def test_enrichment_updates_tracked_event_and_recalcs_cadence() -> None:
    svc = _svc()
    svc.enroll("boshow", "ev1", now=NOW)
    created = svc.generate_due_jobs(NOW)
    trace = await svc.process_job(created[0], NOW)
    assert trace["enrichment"]["outcome"] in ("ENRICHMENT_SUCCEEDED", "ENRICHMENT_PARTIAL")
    with SessionLocal() as s:
        te = s.execute(select(TrackedEvent)).scalar_one()
    assert te.starts_at is not None                       # date filled from enrichment
    assert te.city == "Kolkata" and te.region_id == "region:west-bengal"  # venue geography
    assert te.cadence_reason != "no_event_date"           # cadence recalculated from the resolved date
    assert te.last_enriched_at is not None


async def test_enrichment_disabled_preserves_phase2_behaviour() -> None:
    cfg = Settings(scheduled_capture_enabled=True, capture_enrichment_enabled=False)
    enr = EnrichmentService(SessionLocal, StubGraphReader(_NODE, _NEIGHBORS), None, cfg)
    svc = SchedulerService(SessionLocal, StubCapturer(default=_present("ev1")), cfg, enricher=enr)
    svc.enroll("boshow", "ev1", now=NOW)
    created = svc.generate_due_jobs(NOW)
    trace = await svc.process_job(created[0], NOW)
    assert "enrichment" not in trace  # disabled -> no enrichment step
    with SessionLocal() as s:
        te = s.execute(select(TrackedEvent)).scalar_one()
    assert te.starts_at is None and te.cadence_reason == "no_event_date"  # unchanged Phase 2 fallback


async def test_enrichment_failure_does_not_fail_capture() -> None:
    class Boom:
        async def get_event(self, event_id):
            raise RuntimeError("graph down")

    cfg = Settings(scheduled_capture_enabled=True, capture_enrichment_enabled=True)
    enr = EnrichmentService(SessionLocal, Boom(), None, cfg)
    svc = SchedulerService(SessionLocal, StubCapturer(default=_present("ev1")), cfg, enricher=enr)
    svc.enroll("boshow", "ev1", now=NOW)
    created = svc.generate_due_jobs(NOW)
    trace = await svc.process_job(created[0], NOW)
    assert trace["result_code"] == SUCCESS_RECORD_PRESENT and trace["job_status"] == "SUCCEEDED"
    # enrichment reported no evidence / handled gracefully; capture unaffected
    with SessionLocal() as s:
        te = s.execute(select(TrackedEvent)).scalar_one()
    assert te.capture_count == 1
