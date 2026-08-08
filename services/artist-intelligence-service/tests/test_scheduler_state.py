"""Phase 5A.2 — read-only scheduler-state + identity reason surfacing for the inspection console."""

from datetime import UTC, datetime, timedelta

from artist_intelligence_service import identity as idlib, intelligence
from artist_intelligence_service.models import ArtistExternalIdentity, DemandRefreshJob
from artist_intelligence_service.providers.base import PROVIDER_YOUTUBE, UNRESOLVED


def _job(db, *, status, scheduled_at, completed_at=None, lock_expires_at=None, artist="artist:x",
         job_type="YOUTUBE_CHANNEL_SNAPSHOT"):
    now = datetime.now(UTC)
    dedup = f"{artist}|{PROVIDER_YOUTUBE}|{job_type}|{scheduled_at.isoformat()}|{status}"
    row = DemandRefreshJob(
        id=idlib.new_id(dedup), dedup_key=dedup, canonical_artist_id=artist,
        provider=PROVIDER_YOUTUBE, job_type=job_type, status=status, scheduled_at=scheduled_at,
        completed_at=completed_at, lock_expires_at=lock_expires_at, attempt_count=1,
        created_at=now, updated_at=now, detail={})
    db.add(row)
    db.flush()
    return row


def test_scheduler_state_counts_and_timestamps(db):
    now = datetime.now(UTC)
    _job(db, status="PENDING", scheduled_at=now + timedelta(hours=6))
    _job(db, status="FAILED_RETRYABLE", scheduled_at=now + timedelta(minutes=30))
    _job(db, status="SUCCEEDED", scheduled_at=now - timedelta(days=1),
         completed_at=now - timedelta(hours=2))
    _job(db, status="FAILED_TERMINAL", scheduled_at=now - timedelta(days=1))
    # a RUNNING job whose lease is still valid counts as running
    _job(db, status="RUNNING", scheduled_at=now, lock_expires_at=now + timedelta(minutes=5))
    # a RUNNING job whose lease expired is reclaimable, NOT counted as running
    _job(db, status="RUNNING", scheduled_at=now - timedelta(seconds=1),
         lock_expires_at=now - timedelta(minutes=5))

    state = intelligence.build_scheduler_state(db, now=now)
    assert state["queued_due"] == 1
    assert state["retrying"] == 1
    assert state["succeeded"] == 1
    assert state["terminal_failures"] == 1
    assert state["running_leased"] == 1  # only the non-expired lease
    assert state["jobs_total"] == 6
    assert state["latest_successful_refresh"] is not None
    # next scheduled = earliest of PENDING / FAILED_RETRYABLE
    assert state["next_scheduled_refresh"].startswith((now + timedelta(minutes=30)).isoformat()[:13])
    # read-only: no action fields are exposed
    assert "actions" not in state and "run_now" not in state


def test_scheduler_state_empty(db):
    state = intelligence.build_scheduler_state(db)
    assert state["jobs_total"] == 0
    assert state["latest_successful_refresh"] is None
    assert state["next_scheduled_refresh"] is None


def test_identity_dict_surfaces_reason_and_invalidation(db):
    now = datetime.now(UTC)
    row = ArtistExternalIdentity(
        id="id1", canonical_artist_id="artist:x", provider=PROVIDER_YOUTUBE,
        identity_type="CHANNEL_ID", provider_id="UCdead", status=UNRESOLVED,
        confidence=0.0, first_seen_at=now, created_at=now, updated_at=now,
        identity_metadata={"reason": "all_leaders_provider_not_found",
                           "invalidation_reason": "PROVIDER_ID_NOT_FOUND"},
        provenance={})
    db.add(row)
    db.flush()
    d = intelligence._identity_dict(row)
    assert d["status"] == UNRESOLVED
    assert d["reason"] == "all_leaders_provider_not_found"
    assert d["invalidation_reason"] == "PROVIDER_ID_NOT_FOUND"
    # bounded slice — never leaks the raw candidate/provider payload
    assert "candidates" not in d and "metadata_json" not in d
