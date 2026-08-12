"""Phase 5B.1 — artist intake & research watchlists.

Canonical ownership stays in crawl (FakeCrawl stands in for the crawl-owned create/match path). A watch
target is an operator instruction, never a canonical artist: it links to an existing canonical or promotes
through the EXISTING evidence rules, and a pasted YouTube hint is confirmed by channels.list before any
identity resolves. Pausing suspends recurring collection without deleting history.
"""

from sqlalchemy import func, select

from artist_intelligence_service import candidates as cand
from artist_intelligence_service import watchlist
from artist_intelligence_service.models import (
    ArtistCandidate,
    ArtistExternalIdentity,
    ArtistWatchTarget,
    DemandRefreshJob,
)
from artist_intelligence_service.providers.youtube import YouTubeProvider
from artist_intelligence_service.scheduler import JOB_IDENTITY, DemandScheduler
from artist_intelligence_service.service import DemandService
from artist_intelligence_service.yturl import CHANNEL_ID, HANDLE, VIDEO_ID, parse_youtube_hint
from tests.conftest import FakeSignal, candidate

CID = "UC" + "a" * 22          # a syntactically valid channel id
CID_URL = f"https://www.youtube.com/channel/{CID}"


class FakeCrawl:
    def __init__(self, existing=None):
        import re
        self._re = re
        self.existing = {self._n(k): v for k, v in (existing or {}).items()}
        self.create_calls = []

    def _n(self, s):
        return self._re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

    async def find_artist_by_name(self, name):
        cid = self.existing.get(self._n(name))
        return {"canonical_entity_id": cid, "canonical_name": name} if cid else None

    async def create_artist(self, name, *, provenance=None, source=None):
        self.create_calls.append(name)
        cid = "artist:" + self._re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return {"canonical_entity_id": cid, "entity_type": "ARTIST", "created": True}

    async def artists(self, *, limit=200, offset=0):
        return []


def _svc(*, found=None):
    signal = FakeSignal(
        search={"arijit singh": [candidate("UC_search_arijit", "Arijit Singh", handle="@arijitsingh",
                                            topic=True)]},
        channel={CID: {"subscriber_count": 100, "total_view_count": 1000, "video_count": 10}},
        videos={CID: [{"video_id": "vid00000001", "title": "Live", "published_at": "2026-07-01T00:00:00Z",
                       "views": 1, "likes": 1, "comments": 1}]},
        found=found)
    return DemandService(youtube=YouTubeProvider(signal=signal))


def _sched():
    return DemandScheduler(service=_svc())


# ---- yturl parsing -----------------------------------------------------------------------------
def test_parse_youtube_hint_forms():
    assert parse_youtube_hint(CID_URL).value == parse_youtube_hint(CID).value  # channel url == bare id
    assert parse_youtube_hint(CID).kind == CHANNEL_ID
    assert parse_youtube_hint("https://youtube.com/@anuvjain").kind == HANDLE
    assert parse_youtube_hint("@anuvjain").value == "anuvjain"
    assert parse_youtube_hint("https://youtu.be/dQw4w9WgXcQ").kind == VIDEO_ID
    assert parse_youtube_hint("https://www.youtube.com/watch?v=dQw4w9WgXcQ").value == "dQw4w9WgXcQ"
    assert parse_youtube_hint("not a url at all !!") is None
    assert parse_youtube_hint("") is None


# ---- 1. create name-only --------------------------------------------------------------------
async def test_name_only_stays_pending_and_creates_no_canonical(db):
    crawl = FakeCrawl()
    out = await watchlist.add_and_resolve(db, display_name="Totally New Act", created_by="op@x.com",
                                          crawl=crawl, scheduler=_sched(), svc=_svc())
    t = out["target"]
    assert out["created"] is True
    assert t["status"] == watchlist.RESOLUTION_PENDING
    assert t["canonical_artist_id"] is None
    assert crawl.create_calls == []                        # (6) never fabricates a canonical


# ---- 2. create from existing canonical ------------------------------------------------------
async def test_operator_selected_existing_canonical_watches(db):
    out = await watchlist.add_and_resolve(db, display_name="Anuv Jain", created_by="op@x.com",
                                          canonical_artist_id="artist:anuv-jain",
                                          crawl=FakeCrawl(), scheduler=_sched(), svc=_svc())
    t = out["target"]
    assert t["canonical_artist_id"] == "artist:anuv-jain"
    assert t["status"] == watchlist.WATCHING


# ---- 3. create with a YouTube channel URL ---------------------------------------------------
async def test_channel_url_resolves_verified_identity(db):
    out = await watchlist.add_and_resolve(db, display_name="Arijit Singh", created_by="op@x.com",
                                          canonical_artist_id="artist:arijit-singh",
                                          youtube_hint=CID_URL,
                                          crawl=FakeCrawl(), scheduler=_sched(), svc=_svc())
    t = out["target"]
    assert t["youtube_channel_id"] == CID
    assert t["youtube_identity_state"] == "RESOLVED"
    assert t["human_state"] == "Watching"
    ident = db.execute(select(ArtistExternalIdentity).where(
        ArtistExternalIdentity.provider_id == CID)).scalar_one()
    assert ident.status == "RESOLVED" and ident.last_verified_at is not None


# ---- 4. duplicate intake is idempotent ------------------------------------------------------
async def test_duplicate_intake_is_idempotent(db):
    a = await watchlist.add_and_resolve(db, display_name="Anuv Jain", created_by="op@x.com",
                                        canonical_artist_id="artist:anuv-jain", crawl=FakeCrawl(),
                                        scheduler=_sched(), svc=_svc())
    b = await watchlist.add_and_resolve(db, display_name="Anuv Jain", created_by="op2@x.com",
                                        canonical_artist_id="artist:anuv-jain", crawl=FakeCrawl(),
                                        scheduler=_sched(), svc=_svc())
    assert a["target"]["id"] == b["target"]["id"]
    assert b["created"] is False
    assert db.execute(select(func.count()).select_from(ArtistWatchTarget)).scalar_one() == 1


# ---- 5. bulk intake bounded + idempotent ----------------------------------------------------
async def test_bulk_intake_bounded_and_idempotent(db, monkeypatch):
    monkeypatch.setattr(watchlist.settings, "watchlist_bulk_max", 3)
    names = ["Anuv Jain", "Prateek Kuhad", "Hanumankind", "Peter Cat Recording Co.", "Anuv Jain"]
    out = await watchlist.add_bulk(db, watchlist.parse_bulk_names("\n".join(names)),
                                   created_by="op@x.com", crawl=FakeCrawl(), scheduler=_sched(),
                                   svc=_svc())
    assert out["created"] == 3                              # bounded to 3, dedup collapsed the repeat
    # re-run identical batch → no new rows
    out2 = await watchlist.add_bulk(db, watchlist.parse_bulk_names("\n".join(names)),
                                    created_by="op@x.com", crawl=FakeCrawl(), scheduler=_sched(),
                                    svc=_svc())
    assert out2["created"] == 0
    assert db.execute(select(func.count()).select_from(ArtistWatchTarget)).scalar_one() == 3


# ---- 7. existing canonical match links without duplicate ------------------------------------
async def test_name_matches_existing_canonical_links(db):
    crawl = FakeCrawl(existing={"Arijit Singh": "artist:arijit-singh"})
    out = await watchlist.add_and_resolve(db, display_name="Arijit Singh", created_by="op@x.com",
                                          crawl=crawl, scheduler=_sched(), svc=_svc())
    assert out["target"]["canonical_artist_id"] == "artist:arijit-singh"
    assert out["target"]["status"] == watchlist.WATCHING
    assert crawl.create_calls == []                        # matched, not created


# ---- 8. sufficient evidence uses the existing promotion path --------------------------------
async def test_multi_source_uses_promotion_and_creates_via_crawl(db):
    # an independent EVENT candidate already exists for the same name → operator intake is the 2nd source
    cand.upsert_candidate(db, display_name="Rising Band", discovery_source=cand.SRC_EVENT,
                          discovery_source_id="ev1")
    crawl = FakeCrawl()
    out = await watchlist.add_and_resolve(db, display_name="Rising Band", created_by="op@x.com",
                                          crawl=crawl, scheduler=_sched(), svc=_svc())
    assert out["target"]["canonical_artist_id"] == "artist:rising-band"
    assert crawl.create_calls == ["Rising Band"]           # created through the crawl owner, not here


# ---- 9. insufficient evidence remains pending ----------------------------------------------
async def test_insufficient_evidence_pending(db):
    out = await watchlist.add_and_resolve(db, display_name="Obscure Solo", created_by="op@x.com",
                                          youtube_hint="@obscuresolo", crawl=FakeCrawl(),
                                          scheduler=_sched(), svc=_svc())
    # single operator source, no existing canonical → pending, and the hint is NOT applied (no canonical)
    assert out["target"]["status"] == watchlist.RESOLUTION_PENDING
    assert out["target"]["canonical_artist_id"] is None
    assert db.execute(select(func.count()).select_from(ArtistExternalIdentity)).scalar_one() == 0


# ---- 10. YouTube hint still requires provider verification ----------------------------------
async def test_hint_requires_verification(db):
    # channel id is syntactically valid but verifies CHANNEL_NOT_FOUND → never becomes a resolved identity
    out = await watchlist.add_and_resolve(
        db, display_name="Arijit Singh", created_by="op@x.com",
        canonical_artist_id="artist:arijit-singh", youtube_hint=CID_URL,
        crawl=FakeCrawl(), scheduler=_sched(), svc=_svc(found=[]))
    assert out["target"]["youtube_channel_id"] is None
    assert out["target"]["youtube_identity_state"] != "RESOLVED"
    resolved = db.execute(select(func.count()).select_from(ArtistExternalIdentity)
                          .where(ArtistExternalIdentity.status == "RESOLVED")).scalar_one()
    assert resolved == 0


# ---- 11. successful target enters the existing demand workflow ------------------------------
async def test_watching_enrolls_in_demand_pipeline(db):
    await watchlist.add_and_resolve(db, display_name="Anuv Jain", created_by="op@x.com",
                                    canonical_artist_id="artist:anuv-jain", crawl=FakeCrawl(),
                                    scheduler=_sched(), svc=_svc())
    # onboarding queued an identity-discovery job through the existing scheduler (no parallel path)
    jobs = db.execute(select(func.count()).select_from(DemandRefreshJob)
                      .where(DemandRefreshJob.job_type == JOB_IDENTITY,
                             DemandRefreshJob.canonical_artist_id == "artist:anuv-jain")).scalar_one()
    assert jobs == 1


# ---- 12 + 13. pause suspends scheduling; resume restores it ---------------------------------
async def _watching_with_resolved_identity(db):
    return await watchlist.add_and_resolve(
        db, display_name="Arijit Singh", created_by="op@x.com",
        canonical_artist_id="artist:arijit-singh", youtube_hint=CID_URL,
        crawl=FakeCrawl(), scheduler=_sched(), svc=_svc())


async def test_pause_prevents_recurring_scheduling(db):
    out = await _watching_with_resolved_identity(db)
    target = watchlist.get_target(db, out["target"]["id"])
    watchlist.pause_target(db, target)
    enq = _sched().enqueue_due(db)
    assert enq["paused_artists_skipped"] >= 1
    assert db.execute(select(func.count()).select_from(DemandRefreshJob)
                      .where(DemandRefreshJob.canonical_artist_id == "artist:arijit-singh",
                             DemandRefreshJob.job_type != JOB_IDENTITY)).scalar_one() == 0


async def test_resume_restores_scheduling(db):
    out = await _watching_with_resolved_identity(db)
    target = watchlist.get_target(db, out["target"]["id"])
    watchlist.pause_target(db, target)
    _sched().enqueue_due(db)
    await watchlist.resume_target(db, target, crawl=FakeCrawl(), scheduler=_sched(), svc=_svc())
    enq = _sched().enqueue_due(db)
    assert enq["jobs_created"] >= 1


# ---- diagnostics -------------------------------------------------------------------------------
async def test_diagnostics_counts(db):
    await watchlist.add_and_resolve(db, display_name="Anuv Jain", created_by="op@x.com",
                                    canonical_artist_id="artist:anuv-jain", crawl=FakeCrawl(),
                                    scheduler=_sched(), svc=_svc())
    await watchlist.add_and_resolve(db, display_name="Nobody At All", created_by="op@x.com",
                                    crawl=FakeCrawl(), scheduler=_sched(), svc=_svc())
    d = watchlist.diagnostics(db)
    assert d["total"] == 2
    assert d["watching"] == 1
    assert d["resolution_pending"] == 1
    assert d["targets_with_canonical_artist"] == 1


async def test_reject_and_reresolve(db):
    crawl = FakeCrawl()
    out = await watchlist.add_and_resolve(db, display_name="Later Star", created_by="op@x.com",
                                          crawl=crawl, scheduler=_sched(), svc=_svc())
    target = watchlist.get_target(db, out["target"]["id"])
    assert target.status == watchlist.RESOLUTION_PENDING
    # now the name has canonical evidence → a bounded re-resolution links it
    crawl2 = FakeCrawl(existing={"Later Star": "artist:later-star"})
    res = await watchlist.reresolve_pending(db, crawl=crawl2, scheduler=_sched(), svc=_svc())
    assert res["now_resolved"] == 1
    assert watchlist.get_target(db, target.id).canonical_artist_id == "artist:later-star"
