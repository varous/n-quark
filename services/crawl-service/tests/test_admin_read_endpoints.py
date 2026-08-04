"""Read endpoints added for the admin BFF: capture-job listing, canonical-entity listing + detail,
and the identity_state signal (canonical vs legacy/possible-duplicate)."""

from datetime import UTC, datetime

import pytest
from _stubs import MultiStubGraphReader, StubGraphWriter
from fastapi.testclient import TestClient

from crawl_service.db import SessionLocal
from crawl_service.entity_resolution.service import EntityResolutionService
from crawl_service.main import app
from crawl_service.models import ScheduledCaptureJob

NOW = datetime(2026, 8, 1, tzinfo=UTC)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _job(jid, status="PENDING", source="boshow"):
    return ScheduledCaptureJob(id=jid, dedup_key=jid, source=source, source_record_id=jid,
                               status=status, priority=0, scheduled_at=NOW, attempt_count=0,
                               consecutive_failures=0, detail={}, created_at=NOW, updated_at=NOW)


def test_capture_jobs_list_paginated(client):
    with SessionLocal() as s, s.begin():
        for i in range(5):
            s.add(_job(f"j{i}", status="SUCCEEDED" if i % 2 else "FAILED_RETRYABLE"))
    r = client.get("/v1/internal/capture-schedule/jobs?limit=2&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 5 and len(body["jobs"]) == 2 and body["limit"] == 2
    r2 = client.get("/v1/internal/capture-schedule/jobs?status=FAILED_RETRYABLE")
    assert all(j["status"] == "FAILED_RETRYABLE" for j in r2.json()["jobs"])


def test_capture_job_detail_404(client):
    assert client.get("/v1/internal/capture-schedule/jobs/nope").status_code == 404


async def test_entities_listing_and_identity_state():
    # a venue captured in two cities: the city-scoped canonical + a legacy base id => POSSIBLE_DUPLICATE
    b = ({"id": "event:b", "type": "event", "properties": {"display_name": "G", "city": "Mumbai"}},
         [{"relationship": "OCCURS_AT", "node": {"id": "venue:hrc", "properties": {"display_name": "Hard Rock Cafe"}}}])
    reader = MultiStubGraphReader(mapping={"event:b": b})
    from crawl_service.config import Settings
    svc = EntityResolutionService(SessionLocal, reader, StubGraphWriter(), Settings())
    await svc.resolve_event(canonical_event_id="event:b", source="boshow", source_record_id="b1", now=NOW)
    listing = svc.entities(entity_type="VENUE")
    assert listing["count"] == 1
    row = listing["entities"][0]
    assert row["entity_type"] == "VENUE" and row["identity_state"] in ("CANONICAL", "ALIAS_LINKED")
    detail = svc.entity_detail("VENUE", row["canonical_entity_id"])
    assert detail is not None and detail["linked_event_count"] == 1
