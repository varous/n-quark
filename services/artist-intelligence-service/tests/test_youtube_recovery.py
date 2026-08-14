"""Phase 5B.2.7 — YouTube collection recovery.

Proves the three unblocking fixes: (1) ambiguous-by-snippet candidates now reach AUTHORITATIVE
channels.list verification and can resolve on the enriched metadata (while equally-named impostors stay
ambiguous); (2) the quota model is the current granular one — search = 1 unit in an independent
100-call quota, never double-counted into the general pool; (3) AMBIGUOUS/UNRESOLVED identities stay
schedulable (re-enqueued), and invalid canonicals are excluded from monitoring.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from artist_intelligence_service import config, quota
from artist_intelligence_service.models import ArtistExternalIdentity, DemandRefreshJob
from artist_intelligence_service.providers.base import PROVIDER_YOUTUBE
from artist_intelligence_service.providers.youtube import YouTubeProvider
from artist_intelligence_service.scheduler import JOB_IDENTITY, DemandScheduler
from artist_intelligence_service.service import DemandService
from tests.conftest import FakeSignal, candidate

pytestmark = pytest.mark.asyncio

ARTIST = "artist:anuv-jain"


def _svc(fake):
    return DemandService(youtube=YouTubeProvider(signal=fake))


class FakeCrawlRegistry:
    """Registry stand-in: `artists()` returns the valid canonical cohort (what enqueue eligibility reads)."""
    def __init__(self, ids):
        self._ids = list(ids)

    async def artists(self, *, limit=200, offset=0):
        return [{"canonical_entity_id": i} for i in self._ids]


# ---- §5/§6: quota semantics ---------------------------------------------------------------------
async def test_search_debits_one_unit_independent_of_general(db):
    # a search that also verifies: search bucket += 1 (not 100); general read += 1 (channels.list)
    fake = FakeSignal(search={"anuv jain": [candidate("UC_a", "Anuv Jain", topic=True)]},
                      channel={"UC_a": {"title": "Anuv Jain", "subscriber_count": 1}})
    await _svc(fake).resolve_youtube(db, ARTIST, query="Anuv Jain")
    snap = quota.bucket_snapshot(db, PROVIDER_YOUTUBE)
    assert snap["buckets"]["SEARCH"]["used"] == 1
    assert snap["search_queries"]["cost_per_call"] == 1 and snap["search_queries"]["independent"] is True
    assert snap["buckets"]["GENERAL_READ"]["used"] >= 1


async def test_quota_reconciles_no_double_count(db):
    # 35 searches debit exactly 35 SEARCH units — the general total never absorbs SEARCH or its
    # SEARCH:<purpose> sub-buckets (the historical 2x double-count).
    m = quota.QuotaMeter()
    for _ in range(35):
        m.search(purpose=quota.SEARCH_UNRESOLVED)
    quota.record_meter(db, PROVIDER_YOUTUBE, m)
    snap = quota.bucket_snapshot(db, PROVIDER_YOUTUBE)
    assert snap["buckets"]["SEARCH"]["used"] == 35
    assert snap["general_pool"]["used"] == 0        # search is NOT in the general pool
    assert snap["used_total"] == 0                   # legacy total is now the honest general-only total
    assert snap["reconciles"] is True


# ---- §7/§8: ambiguous → authoritative verification ---------------------------------------------
async def test_ambiguous_snippet_resolves_via_authoritative_verification(db):
    # Search snippet is ambiguous (exact name but no topic signal → 0.5 < 0.70 threshold). channels.list
    # authoritatively confirms the exact title + music topic → the artist RESOLVES, and GENERAL_READ fires.
    fake = FakeSignal(
        search={"anuv jain": [candidate("UC_real", "Anuv Jain", topic=False),
                              candidate("UC_noise", "Anuv Jain Reaction", topic=False)]},
        channel={"UC_real": {"title": "Anuv Jain", "topic_categories": ["Music"],
                             "subscriber_count": 100}})
    out = await _svc(fake).resolve_youtube(db, ARTIST, query="Anuv Jain")
    assert out["status"] == "RESOLVED" and out["verified"] is True
    assert out["provider_id"] == "UC_real"
    assert fake.calls["verify"] >= 1                       # channels.list actually invoked
    snap = quota.bucket_snapshot(db, PROVIDER_YOUTUBE)
    assert snap["buckets"]["GENERAL_READ"]["used"] >= 1


async def test_two_equally_named_verified_channels_stay_ambiguous(db):
    # Both candidates authoritatively verify with the exact title → no clear leader by margin → AMBIGUOUS.
    # A verified channel does NOT resolve merely because the name matches (no invented certainty).
    fake = FakeSignal(
        search={"common name": [candidate("UC_1", "Common Name"), candidate("UC_2", "Common Name")]},
        channel={"UC_1": {"title": "Common Name"}, "UC_2": {"title": "Common Name"}})
    out = await _svc(fake).resolve_youtube(db, "artist:common-name", query="Common Name")
    assert out["status"] == "AMBIGUOUS" and out["reason"] == "verified_no_clear_leader"
    assert fake.calls["verify"] >= 2                       # both plausible candidates were verified


async def test_search_success_does_not_imply_identity_resolved(db):
    # A candidate that does NOT exist at the provider (channels.list CHANNEL_NOT_FOUND) → UNRESOLVED,
    # even though the search HTTP call succeeded.
    fake = FakeSignal(search={"ghost act": [candidate("UC_missing", "Ghost Act")]}, found=set())
    out = await _svc(fake).resolve_youtube(db, "artist:ghost-act", query="Ghost Act")
    assert out["verified"] is False and out["status"] == "UNRESOLVED"
    assert out["reason"] == "all_candidates_provider_not_found"


# ---- §9/§10: scheduler keeps non-RESOLVED schedulable; excludes invalid canonicals --------------
def _seed_identity(db, artist, status, *, query=None):
    now = datetime.now(UTC)
    db.add(ArtistExternalIdentity(
        id="pending:" + artist, canonical_artist_id=artist, provider=PROVIDER_YOUTUBE,
        identity_type="CHANNEL_ID", provider_id="pending:" + artist, status=status,
        confidence=0.0, first_seen_at=now, created_at=now, updated_at=now,
        identity_metadata={"query": query or artist}, provenance={}))
    db.commit()


async def test_scheduler_reenqueues_unresolved_identity(db):
    _seed_identity(db, ARTIST, "UNRESOLVED", query="Anuv Jain")
    sched = DemandScheduler(service=_svc(FakeSignal()), crawl=FakeCrawlRegistry([ARTIST]))
    out = await sched.enqueue_identity_reattempts(db)
    assert out["jobs_created"] == 1 and out["eligible"] == 1
    job = db.execute(select(DemandRefreshJob).where(DemandRefreshJob.job_type == JOB_IDENTITY)).scalar_one()
    assert job.canonical_artist_id == ARTIST and job.status == "PENDING"
    assert (job.detail or {}).get("display_name") == "Anuv Jain"       # reused the stored query


async def test_reenqueue_excludes_invalid_canonical(db):
    # an identity referencing a canonical NOT in the crawl registry (orphan/compound) is never monitored
    _seed_identity(db, "artist:dxm-india-presents-compound-junk", "AMBIGUOUS")
    sched = DemandScheduler(service=_svc(FakeSignal()), crawl=FakeCrawlRegistry([ARTIST]))
    out = await sched.enqueue_identity_reattempts(db)
    assert out["jobs_created"] == 0 and out["skipped_invalid"] == 1
    assert db.execute(select(DemandRefreshJob)).first() is None


async def test_reenqueue_fails_closed_when_registry_unavailable(db):
    _seed_identity(db, ARTIST, "AMBIGUOUS")

    class DownCrawl:
        async def artists(self, *, limit=200, offset=0):
            raise RuntimeError("crawl down")

    sched = DemandScheduler(service=_svc(FakeSignal()), crawl=DownCrawl())
    out = await sched.enqueue_identity_reattempts(db)
    assert out["jobs_created"] == 0 and out.get("registry_unavailable") is True   # never monitor blindly
