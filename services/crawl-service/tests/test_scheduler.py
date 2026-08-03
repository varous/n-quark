"""SchedulerService integration tests — selection, priority, lease-locking, idempotency, retry,
health accrual and the end-to-end run loop, all with a stub capturer (no network)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from crawl_service.classification import (
    SUCCESS_RECORD_ABSENT,
    SUCCESS_RECORD_PRESENT,
    TIMEOUT,
    CaptureOutcome,
)
from crawl_service.config import Settings, settings
from crawl_service.models import ScheduledCaptureJob, TrackedEvent
from crawl_service.service import SchedulerService

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _svc(session_factory, capturer, config=None):
    return SchedulerService(session_factory, capturer, config or settings)


def _present(sid, *, noop=True, transitions=None):
    return CaptureOutcome(
        SUCCESS_RECORD_PRESENT, http_status=200,
        shadow_result={"noop": noop, "persisted": not noop, "transitions": transitions or []},
        canonical_event_id=f"event:{sid}",
    )


def _set_next(session_factory, sid, when):
    with session_factory() as s, s.begin():
        s.execute(update(TrackedEvent).where(TrackedEvent.source_record_id == sid)
                  .values(next_capture_at=when))


# ---- enrollment / selection ---------------------------------------------------------------------
def test_enroll_respects_source_allowlist(session_factory):
    from _stubs import StubCapturer
    svc = _svc(session_factory, StubCapturer())
    assert svc.enroll("boshow", "ev1", now=NOW) is not None
    assert svc.enroll("bookmyshow", "evX", now=NOW) is None  # not in allow-list


def test_enroll_respects_max_tracked(session_factory):
    cfg = Settings(scheduled_capture_max_tracked=1)
    from _stubs import StubCapturer
    svc = _svc(session_factory, StubCapturer(), cfg)
    assert svc.enroll("boshow", "ev1", now=NOW) is not None
    assert svc.enroll("boshow", "ev2", now=NOW) is None  # cap reached


def test_enroll_is_idempotent(session_factory):
    from _stubs import StubCapturer
    svc = _svc(session_factory, StubCapturer())
    a = svc.enroll("boshow", "ev1", now=NOW)
    b = svc.enroll("boshow", "ev1", now=NOW)
    assert a == b
    with session_factory() as s:
        assert len(s.execute(select(TrackedEvent)).scalars().all()) == 1


# ---- job generation / idempotent windows --------------------------------------------------------
def test_generate_due_job_and_dedup(session_factory):
    from _stubs import StubCapturer
    svc = _svc(session_factory, StubCapturer())
    svc.enroll("boshow", "ev1", now=NOW)  # next_capture_at None -> due ("initial" window)
    created = svc.generate_due_jobs(NOW)
    assert len(created) == 1
    # duplicate cron: same window -> no new job
    assert svc.generate_due_jobs(NOW) == []
    with session_factory() as s:
        assert len(s.execute(select(ScheduledCaptureJob)).scalars().all()) == 1


def test_future_scheduled_event_not_due(session_factory):
    from _stubs import StubCapturer
    svc = _svc(session_factory, StubCapturer())
    svc.enroll("boshow", "ev1", now=NOW)
    _set_next(session_factory, "ev1", NOW + timedelta(hours=5))
    assert svc.generate_due_jobs(NOW) == []


# ---- locking ------------------------------------------------------------------------------------
def test_claim_is_exclusive_across_workers(session_factory):
    from _stubs import StubCapturer
    svc = _svc(session_factory, StubCapturer())
    svc.enroll("boshow", "ev1", now=NOW)
    svc.generate_due_jobs(NOW)
    a = svc.claim_jobs(NOW, "worker-A", limit=10)
    b = svc.claim_jobs(NOW, "worker-B", limit=10)
    assert len(a) == 1 and b == []  # second worker cannot claim the same window


def test_expired_lock_recovered_and_reclaimable(session_factory):
    from _stubs import StubCapturer
    svc = _svc(session_factory, StubCapturer())
    svc.enroll("boshow", "ev1", now=NOW)
    svc.generate_due_jobs(NOW)
    svc.claim_jobs(NOW, "worker-A", limit=10)
    # lock still valid -> not recovered
    assert svc.recover_expired_locks(NOW + timedelta(seconds=10)) == 0
    # lock expired -> recovered and claimable again
    assert svc.recover_expired_locks(NOW + timedelta(seconds=999)) == 1
    reclaim = svc.claim_jobs(NOW + timedelta(seconds=999), "worker-B", limit=10)
    assert len(reclaim) == 1


def test_claim_orders_by_priority(session_factory):
    from _stubs import StubCapturer
    svc = _svc(session_factory, StubCapturer())
    svc.enroll("boshow", "far", starts_at=NOW + timedelta(days=60), now=NOW)
    svc.enroll("boshow", "eventday", starts_at=NOW + timedelta(hours=6), now=NOW)
    svc.generate_due_jobs(NOW)
    claimed = svc.claim_jobs(NOW, "w", limit=1)  # only the highest-priority one
    with session_factory() as s:
        job = s.get(ScheduledCaptureJob, claimed[0])
    assert job.source_record_id == "eventday"


# ---- processing / health / retry ----------------------------------------------------------------
async def test_process_present_updates_health_and_schedules_next(session_factory):
    from _stubs import StubCapturer
    svc = _svc(session_factory, StubCapturer(default=_present("ev1", noop=True)))
    svc.enroll("boshow", "ev1", now=NOW)
    created = svc.generate_due_jobs(NOW)
    trace = await svc.process_job(created[0], NOW)
    assert trace["result_code"] == SUCCESS_RECORD_PRESENT and trace["job_status"] == "SUCCEEDED"
    with session_factory() as s:
        te = s.execute(select(TrackedEvent)).scalar_one()
    assert te.capture_count == 1 and te.canonical_event_id == "event:ev1"
    assert te.distinct_state_count == 0  # no-op shadow result
    assert te.next_capture_at is not None and te.cadence_reason is not None


async def test_process_present_with_transition_counts(session_factory):
    from _stubs import StubCapturer
    out = _present("ev1", noop=False, transitions=[{"transition_type": "PUBLIC_FILL_RATIO_CHANGED"}])
    svc = _svc(session_factory, StubCapturer(default=out))
    svc.enroll("boshow", "ev1", now=NOW)
    created = svc.generate_due_jobs(NOW)
    await svc.process_job(created[0], NOW)
    with session_factory() as s:
        te = s.execute(select(TrackedEvent)).scalar_one()
    assert te.distinct_state_count == 1 and te.transition_count == 1 and te.last_state_change_at is not None


async def test_retryable_failure_backs_off_without_absence(session_factory):
    from _stubs import StubCapturer
    svc = _svc(session_factory, StubCapturer(default=CaptureOutcome(TIMEOUT)))
    svc.enroll("boshow", "ev1", now=NOW)
    created = svc.generate_due_jobs(NOW)
    trace = await svc.process_job(created[0], NOW)
    assert trace["job_status"] == "FAILED_RETRYABLE"
    with session_factory() as s:
        te = s.execute(select(TrackedEvent)).scalar_one()
        job = s.get(ScheduledCaptureJob, created[0])
    assert te.consecutive_failures == 1
    assert te.consecutive_absences == 0  # a failure is NEVER an absence
    assert te.tracking_status == "ACTIVE"
    assert job.next_capture_at is not None and te.cadence_reason == "retry_backoff"


async def test_absence_increments_disappearance_evidence(session_factory):
    from _stubs import StubCapturer
    svc = _svc(session_factory, StubCapturer(default=CaptureOutcome(SUCCESS_RECORD_ABSENT, http_status=404)))
    svc.enroll("boshow", "ev1", now=NOW)
    created = svc.generate_due_jobs(NOW)
    trace = await svc.process_job(created[0], NOW)
    assert trace["job_status"] == "SUCCEEDED"
    with session_factory() as s:
        te = s.execute(select(TrackedEvent)).scalar_one()
    assert te.consecutive_absences == 1 and te.consecutive_failures == 0 and te.last_success_at is not None


# ---- end-to-end run loop ------------------------------------------------------------------------
async def test_run_once_accumulates_multiple_observations(session_factory):
    from _stubs import StubCapturer
    stub = StubCapturer(default=_present("ev1", noop=True))
    svc = _svc(session_factory, stub)
    svc.enroll("boshow", "ev1", now=NOW)

    s1 = await svc.run_once("w1", now=NOW, trace=True)
    assert s1["jobs_created"] == 1 and s1["jobs_claimed"] == 1 and s1["processed"] == 1

    # advance past the scheduled cadence and run again -> a second observation accrues
    _set_next(session_factory, "ev1", NOW + timedelta(hours=1))
    s2 = await svc.run_once("w1", now=NOW + timedelta(hours=2), trace=True)
    assert s2["processed"] == 1
    with session_factory() as s:
        te = s.execute(select(TrackedEvent)).scalar_one()
    assert te.capture_count == 2  # multiple scheduled observations, no manual triggering
    assert len(stub.calls) == 2


async def test_run_once_is_idempotent_on_immediate_rerun(session_factory):
    from _stubs import StubCapturer
    svc = _svc(session_factory, StubCapturer(default=_present("ev1", noop=True)))
    svc.enroll("boshow", "ev1", now=NOW)
    await svc.run_once("w1", now=NOW)
    # immediate rerun: next_capture_at is in the future now -> nothing due
    s2 = await svc.run_once("w1", now=NOW)
    assert s2["jobs_created"] == 0 and s2["jobs_claimed"] == 0


@pytest.mark.parametrize("limit,expected", [(1, 1), (5, 2)])
async def test_max_jobs_per_run(session_factory, limit, expected):
    from _stubs import StubCapturer
    svc = _svc(session_factory, StubCapturer(default=_present("x", noop=True)))
    svc.enroll("boshow", "ev1", now=NOW)
    svc.enroll("boshow", "ev2", now=NOW)
    summary = await svc.run_once("w1", now=NOW, limit=limit)
    assert summary["jobs_claimed"] == expected
