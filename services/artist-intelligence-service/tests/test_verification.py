"""Phase 5A.1a — YouTube identity verification integrity (the production hotfix).

Invariant: a CHANNEL_ID may become RESOLVED only if channels.list confirms it exists at resolution
time; last_verified_at is set only on a successful verification; provider-invalid ids cannot create
observations and leave normal recurring refresh; transient failures never invalidate.
"""

from sqlalchemy import func, select

from artist_intelligence_service.models import ArtistDemandObservation as ADO
from artist_intelligence_service.providers.base import AMBIGUOUS, RESOLVED, UNRESOLVED
from artist_intelligence_service.providers.youtube import YouTubeProvider
from artist_intelligence_service.scheduler import DemandScheduler
from artist_intelligence_service.service import DemandService
from tests.conftest import FakeSignal, candidate

ARTIST = "artist:arijit-singh"

# The exact production evidence — a labeled regression fixture (never hardcoded into app logic).
PROD_STALE_CHANNEL_ID = "UCUEcefFC0sBRZfCTBqcx9jg"


def _svc(fake):
    return DemandService(youtube=YouTubeProvider(signal=fake))


# 1. search candidate scores above threshold but channels.list returns no item -----------------
async def test_stale_candidate_scores_high_but_not_found_is_not_resolved(db):
    fake = FakeSignal(
        search={"arijit singh": [candidate(PROD_STALE_CHANNEL_ID, "Arijit Singh", topic=True)]},
        found=[])  # channels.list → CHANNEL_NOT_FOUND
    out = await _svc(fake).resolve_youtube(db, ARTIST, query="Arijit Singh")
    assert out["status"] != RESOLVED
    assert out["verified"] is False
    assert out["rejected_candidates"][0]["provider_id"] == PROD_STALE_CHANNEL_ID
    assert out["rejected_candidates"][0]["verification_result"] == "CHANNEL_NOT_FOUND"
    ident = _svc(fake).list_identities(db, ARTIST)[0]
    assert ident.last_verified_at is None            # never verified → never stamped


# 2. top candidate invalid, second valid but BELOW score threshold → do not resolve second ------
async def test_next_candidate_below_threshold_not_resolved(db):
    fake = FakeSignal(
        search={"maya": [
            candidate("UC_top_invalid", "Maya", topic=True),      # 0.8, clear leader
            candidate("UC_second_weak", "Maya Fan Covers")]},     # weak partial, ~0
        found=["UC_second_weak"])                                  # top NOT_FOUND, second exists
    out = await _svc(fake).resolve_youtube(db, ARTIST, query="Maya")
    assert out["status"] != RESOLVED                               # second too weak to resolve
    assert any(r["provider_id"] == "UC_top_invalid" for r in out["rejected_candidates"])


# 3. top invalid, second valid AND independently satisfies thresholds → may resolve second ------
async def test_next_candidate_that_satisfies_thresholds_resolves(db):
    fake = FakeSignal(
        search={"maya": [
            candidate("UC_top_invalid", "Maya", handle="mayaofficial", topic=True),  # +known_handle → 1.0
            candidate("UC_second_ok", "Maya", topic=True)]},                          # exact+topic → 0.8
        found=["UC_second_ok"])                                    # top NOT_FOUND; second FOUND
    out = await _svc(fake).resolve_youtube(db, ARTIST, query="Maya",
                                           hints={"known_handles": ["mayaofficial"]})
    assert out["status"] == RESOLVED
    assert out["provider_id"] == "UC_second_ok"
    assert any(r["provider_id"] == "UC_top_invalid" for r in out["rejected_candidates"])


# 4. valid candidate: exact id confirmed → RESOLVED + last_verified_at populated ----------------
async def test_valid_candidate_resolves_and_stamps_verification(db):
    fake = FakeSignal(search={"nucleya": [candidate("UC_nuc", "Nucleya", topic=True)]},
                      found=["UC_nuc"])
    out = await _svc(fake).resolve_youtube(db, "artist:nucleya", query="Nucleya")
    assert out["status"] == RESOLVED and out["verified"] is True
    ident = next(i for i in _svc(fake).list_identities(db, "artist:nucleya")
                 if i.provider_id == "UC_nuc")
    assert ident.last_verified_at is not None
    assert ident.provenance.get("verification_method") == "channels.list"
    assert ident.provenance.get("verified_provider_id") == "UC_nuc"


# 5. provider verification network failure → no false NOT_FOUND, no invalidation ----------------
async def test_verification_network_failure_does_not_resolve_or_invalidate(db):
    fake = FakeSignal(search={"nucleya": [candidate("UC_nuc", "Nucleya", topic=True)]},
                      found=["UC_nuc"])
    fake.fail_verify = True
    out = await _svc(fake).resolve_youtube(db, "artist:nucleya", query="Nucleya")
    assert out["status"] == AMBIGUOUS and out["reason"] == "verification_unavailable"
    assert out["verified"] is False
    ident = _svc(fake).list_identities(db, "artist:nucleya")[0]
    assert ident.last_verified_at is None             # transient failure never stamps


# 6. resolved identity later returns CHANNEL_NOT_FOUND during refresh ---------------------------
async def test_resolved_then_gone_invalidates_and_stops_refresh(db):
    fake = FakeSignal(
        search={"nucleya": [candidate("UC_nuc", "Nucleya", topic=True)]},
        channel={"UC_nuc": {"subscriber_count": 1000000, "total_view_count": 5e8, "video_count": 90}},
        videos={"UC_nuc": [{"video_id": "v1", "views": 100, "likes": 5, "comments": 1}]},
        found=["UC_nuc"])
    svc = _svc(fake)
    await svc.resolve_youtube(db, "artist:nucleya", query="Nucleya")
    db.commit()
    # channel disappears from YouTube
    fake._found = []
    out = await svc.snapshot_youtube(db, "artist:nucleya", include_videos=True)
    assert out["status"] == "PROVIDER_ID_NOT_FOUND"
    assert db.execute(select(func.count()).select_from(ADO)).scalar_one() == 0   # no observations
    ident = next(i for i in svc.list_identities(db, "artist:nucleya") if i.provider_id == "UC_nuc")
    assert ident.status == UNRESOLVED
    assert ident.identity_metadata.get("invalidation_reason") == "PROVIDER_ID_NOT_FOUND"
    db.commit()
    # scheduler no longer enqueues jobs for the invalidated identity → no continual retry
    enq = DemandScheduler(service=svc).enqueue_due(db)
    assert enq["jobs_created"] == 0


# 7. repeated successful verification is idempotent (no dup identities/observations) ------------
async def test_repeated_verification_idempotent(db):
    fake = FakeSignal(
        search={"nucleya": [candidate("UC_nuc", "Nucleya", topic=True)]},
        channel={"UC_nuc": {"subscriber_count": 1000000, "total_view_count": 5e8, "video_count": 90}},
        found=["UC_nuc"])
    svc = _svc(fake)
    await svc.resolve_youtube(db, "artist:nucleya", query="Nucleya")
    await svc.resolve_youtube(db, "artist:nucleya", query="Nucleya")
    first = await svc.snapshot_youtube(db, "artist:nucleya", include_videos=False)
    second = await svc.snapshot_youtube(db, "artist:nucleya", include_videos=False)
    resolved = [i for i in svc.list_identities(db, "artist:nucleya") if i.provider_id == "UC_nuc"]
    assert len(resolved) == 1
    assert first["channel_observations"]["created"] == 3
    assert second["channel_observations"]["created"] == 0


# 8. quota accounting includes verification calls ----------------------------------------------
async def test_quota_includes_verification(db):
    from artist_intelligence_service.models import ProviderQuotaDay
    fake = FakeSignal(search={"nucleya": [candidate("UC_nuc", "Nucleya", topic=True)]},
                      found=["UC_nuc"])
    await _svc(fake).resolve_youtube(db, "artist:nucleya", query="Nucleya")
    row = db.execute(select(ProviderQuotaDay)).scalar_one()
    assert row.search_requests == 1               # one search (discovery)
    assert row.non_search_quota_units >= 1        # one channels.list verification read
