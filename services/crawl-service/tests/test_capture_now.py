"""Targeted capture-now (Admin Phase B): uses the normal scheduler/job path, is idempotent within a
window, triggers the Shadow Ledger, and a failed request never becomes absence."""

from datetime import UTC, datetime

from _stubs import StubCapturer
from sqlalchemy import select

from crawl_service.classification import (
    SUCCESS_RECORD_PRESENT,
    SOURCE_UNAVAILABLE,
    CaptureOutcome,
)
from crawl_service.config import Settings
from crawl_service.db import SessionLocal
from crawl_service.models import ScheduledCaptureJob, TrackedEvent
from crawl_service.service import SchedulerService

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _track(source="boshow", sid="ev1", canonical="event:ev1"):
    with SessionLocal() as s, s.begin():
        s.add(TrackedEvent(id="t1", source=source, source_record_id=sid, canonical_event_id=canonical,
                           tracking_status="ACTIVE", first_tracked_at=NOW, priority=0,
                           priority_reason={}, created_at=NOW, updated_at=NOW,
                           consecutive_failures=0, consecutive_absences=0, capture_count=0,
                           distinct_state_count=0, transition_count=0))


def _sched(capturer):
    cfg = Settings(scheduled_capture_enabled=True)
    return SchedulerService(SessionLocal, capturer, cfg)


async def test_capture_now_uses_job_path_and_shadow_ledger():
    _track()
    cap = StubCapturer(default=CaptureOutcome(
        SUCCESS_RECORD_PRESENT, http_status=200,
        shadow_result={"noop": False, "persisted": True, "transitions": [{"transition_type": "EVENT_FIRST_SEEN"}]},
        canonical_event_id="event:ev1"))
    svc = _sched(cap)
    res = await svc.capture_now(source="boshow", source_record_id="ev1", now=NOW)
    assert res["claimed"] is True and res["trace"]["result_code"] == SUCCESS_RECORD_PRESENT
    with SessionLocal() as s:
        job = s.execute(select(ScheduledCaptureJob)).scalar_one()
        te = s.get(TrackedEvent, "t1")
    assert job.status == "SUCCEEDED" and "capture-now" in job.dedup_key
    assert te.capture_count == 1 and te.transition_count == 1  # Shadow Ledger transition counted


async def test_capture_now_idempotent_within_window():
    _track()
    cap = StubCapturer(default=CaptureOutcome(SUCCESS_RECORD_PRESENT, http_status=200,
                                              shadow_result={"noop": True, "persisted": False, "transitions": []},
                                              canonical_event_id="event:ev1"))
    svc = _sched(cap)
    r1 = await svc.capture_now(source="boshow", source_record_id="ev1", now=NOW)
    r2 = await svc.capture_now(source="boshow", source_record_id="ev1", now=NOW)
    assert r1["claimed"] is True and r2["claimed"] is False and r2["dedup_hit"] is True
    with SessionLocal() as s:
        jobs = s.execute(select(ScheduledCaptureJob)).scalars().all()
    assert len(jobs) == 1  # no double job for the same window


async def test_capture_now_untracked_event():
    svc = _sched(StubCapturer())
    res = await svc.capture_now(source="boshow", source_record_id="missing", now=NOW)
    assert res["error"] == "EVENT_NOT_TRACKED"


async def test_capture_now_failure_never_becomes_absence():
    _track()
    cap = StubCapturer(default=CaptureOutcome(SOURCE_UNAVAILABLE, http_status=502))
    svc = _sched(cap)
    res = await svc.capture_now(source="boshow", source_record_id="ev1", now=NOW)
    assert res["trace"]["result_code"] == SOURCE_UNAVAILABLE
    with SessionLocal() as s:
        te = s.get(TrackedEvent, "t1")
    assert te.consecutive_absences == 0  # a failed request is NOT absence
