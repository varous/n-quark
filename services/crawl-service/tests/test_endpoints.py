from collections.abc import Generator

import pytest
from _stubs import StubCapturer
from fastapi.testclient import TestClient

from crawl_service.config import settings
from crawl_service.db import SessionLocal
from crawl_service.deps import get_scheduler
from crawl_service.main import app
from crawl_service.service import SchedulerService


@pytest.fixture()
def stub() -> StubCapturer:
    return StubCapturer()


@pytest.fixture()
def client(stub) -> Generator[TestClient, None, None]:
    svc = SchedulerService(SessionLocal, stub, settings)
    app.dependency_overrides[get_scheduler] = lambda: svc
    with TestClient(app) as c:
        c._svc = svc  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


def test_health_reports_flag(client: TestClient) -> None:
    assert client.get("/health").json()["scheduled_capture_enabled"] is False


def test_run_and_sync_disabled_by_default(client: TestClient) -> None:
    assert client.post("/v1/internal/capture-schedule/run").status_code == 503
    assert client.post("/v1/internal/capture-schedule/sync").status_code == 503


def test_coverage_list_and_detail(client: TestClient) -> None:
    client._svc.enroll("boshow", "ev1")  # type: ignore[attr-defined]
    body = client.get("/v1/internal/capture-schedule").json()
    assert body["count"] == 1 and body["events"][0]["source_record_id"] == "ev1"
    one = client.get("/v1/internal/capture-schedule/boshow/ev1").json()
    assert one["tracking_status"] == "ACTIVE" and one["capture_count"] == 0
    assert client.get("/v1/internal/capture-schedule/boshow/missing").status_code == 404


def test_run_when_enabled_processes_due_event(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "scheduled_capture_enabled", True)
    client._svc.enroll("boshow", "ev1")  # type: ignore[attr-defined]
    summary = client.post("/v1/internal/capture-schedule/run?trace=true").json()
    assert summary["jobs_created"] == 1 and summary["processed"] == 1
    # operational coverage now reflects the capture
    cov = client.get("/v1/internal/capture-schedule/boshow/ev1").json()
    assert cov["capture_count"] == 1 and cov["next_capture_at"] is not None
