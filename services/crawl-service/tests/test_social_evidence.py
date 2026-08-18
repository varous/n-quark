"""Social evidence foundation (Phase 5C.1) — governed spine tests.

Canonical ownership stays with the registry; a social account is external evidence; a SocialMention is
evidence and never a canonical Event; raw expressive content is never persisted; absent authorized
access is honest (deferred/access-pending), never a scrape fallback and never a fabricated failure.
"""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from crawl_service.config import Settings
from crawl_service.models import SocialIdentity, SocialMention, TrackedEvent
from crawl_service.social import SocialAcquisitionService

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
NOW_N = NOW.replace(tzinfo=None)   # SQLite returns naive datetimes on read
LATER = NOW + timedelta(hours=13)  # past the 12h collection interval
CAPTION = "Kolkata! Live in concert 20 Sep 2026 at Aquatica. Tickets live now."


def _cfg(**over):
    c = Settings(social_enabled=True, social_platforms="instagram,facebook")
    for k, v in over.items():
        setattr(c, k, v)
    return c


def _collect_result(platform, account, *, access="MOCK", collectible=True, mentions=None):
    return {"platform": platform.upper(), "account": account, "access_state": access,
            "collectible": collectible, "reason": access, "mentions": mentions or []}


def _mention(post_id="IG_1", chash="hash-1", claims=None):
    # shaped like signal-service SocialClaimMention.to_dict — note: NO raw caption field
    return {"platform": "INSTAGRAM", "source_account": "arijitsingh", "platform_post_id": post_id,
            "post_url": f"https://example.org/{post_id}", "published_at": "2026-09-01T10:00:00+00:00",
            "observed_at": NOW.isoformat(), "evidence_role": "OFFICIAL_ACCOUNT_EVIDENCE",
            "extracted_claims": claims or {"event_name": "Arijit Singh Live", "city": "Kolkata"},
            "linked_handles": [], "confidence": 0.5, "parser_version": "social-claim-extractor-1",
            "content_hash": chash, "provenance": {"acquisition_method": "META_GRAPH_MOCK",
                                                  "raw_content_retention": "EPHEMERAL"}}


def _svc(session_factory, cfg=None, factory=None):
    return SocialAcquisitionService(session_factory, cfg or _cfg(), http_client_factory=factory)


# 1 — canonical ↔ social identity association
def test_link_identity_associates_with_canonical(session_factory):
    svc = _svc(session_factory)
    out = svc.link_identity(canonical_entity_id="artist:arijit-singh", canonical_entity_type="ARTIST",
                            platform="instagram", handle="arijitsingh", now=NOW)
    assert out["created"] and out["platform"] == "INSTAGRAM" and out["audited"]
    with session_factory() as s:
        row = s.execute(select(SocialIdentity)).scalar_one()
    assert row.canonical_entity_id == "artist:arijit-singh" and row.verification_state == "ASSERTED"
    assert row.evidence_role == "OFFICIAL_ACCOUNT_EVIDENCE" and row.active


def test_link_identity_rejects_bad_type_and_platform(session_factory):
    svc = _svc(session_factory)
    with pytest.raises(ValueError):
        svc.link_identity(canonical_entity_id="x:y", canonical_entity_type="EVENT", platform="instagram",
                          handle="h", now=NOW)
    with pytest.raises(ValueError):
        svc.link_identity(canonical_entity_id="x:y", canonical_entity_type="ARTIST", platform="tiktok",
                          handle="h", now=NOW)


# 2 — multiple accounts per canonical (incl. same platform)
def test_multiple_accounts_per_canonical(session_factory):
    svc = _svc(session_factory)
    svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST", platform="instagram",
                      handle="official", now=NOW)
    svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST", platform="instagram",
                      handle="tour", now=NOW)
    svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST", platform="facebook",
                      handle="fbpage", now=NOW)
    got = svc.identities(canonical_entity_id="artist:a")
    assert got["count"] == 3
    # idempotent re-link of the same triple updates, does not duplicate
    again = svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST",
                              platform="instagram", handle="official", now=NOW)
    assert again["created"] is False
    assert svc.identities(canonical_entity_id="artist:a")["count"] == 3


# 3 + 4 + 5 — idempotent ingest, provenance preserved, expressive content NOT retained
@pytest.mark.asyncio
async def test_collect_persists_evidence_idempotently(session_factory):
    async def factory(*, platform, account, evidence_role):
        return _collect_result(platform, account, mentions=[_mention()])
    svc = _svc(session_factory, factory=factory)
    svc.link_identity(canonical_entity_id="artist:arijit-singh", canonical_entity_type="ARTIST",
                      platform="instagram", handle="arijitsingh", now=NOW)
    r1 = await svc.run_once(now=NOW)
    assert r1["mentions_new"] == 1 and r1["collected"] == 1
    r2 = await svc.run_once(now=LATER)                    # same post id + hash → idempotent no-op
    assert r2["mentions_new"] == 0 and r2["mentions_unchanged"] == 1
    with session_factory() as s:
        m = s.execute(select(SocialMention)).scalar_one()
    # provenance preserved
    assert m.provenance.get("acquisition_method") == "META_GRAPH_MOCK"
    assert m.canonical_entity_id == "artist:arijit-singh" and m.content_hash == "hash-1"
    # expressive content is NOT retained: the raw caption appears nowhere on the row
    blob = str(m.extracted_claims) + str(m.provenance) + str(m.__dict__)
    assert CAPTION not in blob
    assert not hasattr(m, "caption") and not hasattr(m, "raw_text")
    # claims survived
    assert m.extracted_claims.get("event_name") == "Arijit Singh Live"


# 3 + 4 — a changed source post creates a SECOND immutable version; the first is never overwritten
@pytest.mark.asyncio
async def test_changed_post_creates_immutable_second_version(session_factory):
    state = {"hash": "hash-1", "claims": {"event_name": "Arijit Singh Live", "venue": "Venue A"}}
    async def factory(*, platform, account, evidence_role):
        return _collect_result(platform, account,
                               mentions=[_mention(chash=state["hash"], claims=state["claims"])])
    svc = _svc(session_factory, factory=factory)
    svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST",
                      platform="instagram", handle="arijitsingh", now=NOW)
    r1 = await svc.run_once(now=NOW)
    assert r1["mentions_new"] == 1
    # the same post is later edited: Venue A → Venue B
    state["hash"] = "hash-2"
    state["claims"] = {"event_name": "Arijit Singh Live", "venue": "Venue B"}
    r2 = await svc.run_once(now=LATER)
    assert r2["mentions_updated"] == 1 and r2["mentions_new"] == 0
    with session_factory() as s:
        rows = s.execute(select(SocialMention).order_by(SocialMention.version)).scalars().all()
    # two immutable versions of ONE logical post
    assert len(rows) == 2
    v1, v2 = rows
    # first version's observed state is UNCHANGED — Venue A still independently queryable
    assert v1.version == 1 and v1.extracted_claims.get("venue") == "Venue A"
    assert v1.content_hash == "hash-1" and v1.is_current is False and v1.superseded_at is not None
    # second version is the current one with the new state
    assert v2.version == 2 and v2.extracted_claims.get("venue") == "Venue B"
    assert v2.content_hash == "hash-2" and v2.is_current is True and v2.superseded_at is None
    # lineage links v2 → v1
    assert v2.previous_mention_id == v1.id and v1.previous_mention_id is None


# 5 — lineage / order between versions is recoverable via the history read
@pytest.mark.asyncio
async def test_version_lineage_is_recoverable(session_factory):
    state = {"hash": "hash-1", "claims": {"venue": "Venue A"}}
    async def factory(*, platform, account, evidence_role):
        return _collect_result(platform, account,
                               mentions=[_mention(chash=state["hash"], claims=state["claims"])])
    svc = _svc(session_factory, factory=factory)
    svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST",
                      platform="instagram", handle="arijitsingh", now=NOW)
    await svc.run_once(now=NOW)
    state["hash"], state["claims"] = "hash-2", {"venue": "Venue B"}
    await svc.run_once(now=LATER)
    hist = svc.mention_history(platform="INSTAGRAM", platform_post_id="IG_1")
    assert hist["versions"] == 2 and hist["revised"] is True
    assert [i["version"] for i in hist["items"]] == [1, 2]
    assert hist["content_hashes"] == ["hash-1", "hash-2"]
    assert [i["extracted_claims"]["venue"] for i in hist["items"]] == ["Venue A", "Venue B"]
    assert hist["items"][1]["previous_mention_id"] == hist["items"][0]["id"]
    assert hist["items"][0]["is_current"] is False and hist["items"][1]["is_current"] is True


# 6 — multiple successive edits preserve every version
@pytest.mark.asyncio
async def test_multiple_edits_preserve_all_versions(session_factory):
    state = {"hash": "h1", "claims": {"venue": "A"}}
    async def factory(*, platform, account, evidence_role):
        return _collect_result(platform, account,
                               mentions=[_mention(chash=state["hash"], claims=state["claims"])])
    svc = _svc(session_factory, factory=factory)
    svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST",
                      platform="instagram", handle="arijitsingh", now=NOW)
    venues = ["A", "B", "C", "D"]
    for i, v in enumerate(venues):
        state["hash"], state["claims"] = f"h{i+1}", {"venue": v}
        await svc.run_once(now=NOW + timedelta(hours=13 * (i + 1)))
    with session_factory() as s:
        rows = s.execute(select(SocialMention).order_by(SocialMention.version)).scalars().all()
    assert [r.extracted_claims["venue"] for r in rows] == venues           # all four preserved in order
    assert [r.is_current for r in rows] == [False, False, False, True]     # only the last is current
    assert sum(r.is_current for r in rows) == 1                            # exactly one current version


# 7 (versions) — raw expressive content is never persisted on ANY version
@pytest.mark.asyncio
async def test_no_raw_content_on_any_version(session_factory):
    state = {"hash": "h1", "claims": {"venue": "A"}}
    async def factory(*, platform, account, evidence_role):
        return _collect_result(platform, account,
                               mentions=[_mention(chash=state["hash"], claims=state["claims"])])
    svc = _svc(session_factory, factory=factory)
    svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST",
                      platform="instagram", handle="arijitsingh", now=NOW)
    await svc.run_once(now=NOW)
    state["hash"], state["claims"] = "h2", {"venue": "B"}
    await svc.run_once(now=LATER)
    with session_factory() as s:
        rows = s.execute(select(SocialMention)).scalars().all()
    for m in rows:
        blob = str(m.extracted_claims) + str(m.provenance) + str(m.__dict__)
        assert CAPTION not in blob
        assert not hasattr(m, "caption") and not hasattr(m, "raw_text")


# robustness — with NO provider content hash, idempotency falls back to the claims themselves
@pytest.mark.asyncio
async def test_versioning_without_content_hash_uses_claims(session_factory):
    state = {"claims": {"venue": "A"}}
    async def factory(*, platform, account, evidence_role):
        return _collect_result(platform, account,
                               mentions=[_mention(chash=None, claims=state["claims"])])
    svc = _svc(session_factory, factory=factory)
    svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST",
                      platform="instagram", handle="arijitsingh", now=NOW)
    await svc.run_once(now=NOW)
    r_same = await svc.run_once(now=LATER)                       # identical claims, no hash → idempotent
    assert r_same["mentions_unchanged"] == 1 and r_same["mentions_new"] == 0
    state["claims"] = {"venue": "B"}                             # changed claims, no hash → new version
    r_changed = await svc.run_once(now=NOW + timedelta(hours=26))
    assert r_changed["mentions_updated"] == 1
    with session_factory() as s:
        rows = s.execute(select(SocialMention).order_by(SocialMention.version)).scalars().all()
    assert [r.extracted_claims["venue"] for r in rows] == ["A", "B"]


# 6 — a mention never becomes a canonical Event
@pytest.mark.asyncio
async def test_mention_does_not_create_event(session_factory):
    async def factory(*, platform, account, evidence_role):
        return _collect_result(platform, account, mentions=[_mention()])
    svc = _svc(session_factory, factory=factory)
    svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST",
                      platform="instagram", handle="arijitsingh", now=NOW)
    await svc.run_once(now=NOW)
    with session_factory() as s:
        events = s.execute(select(TrackedEvent)).scalars().all()
        mentions = s.execute(select(SocialMention)).scalars().all()
    assert events == [] and len(mentions) == 1
    assert mentions[0].processing_status == "UNPROCESSED" and mentions[0].claim_type is None


# 7 — watchlist eligibility + scheduling
@pytest.mark.asyncio
async def test_watchlist_and_scheduling(session_factory):
    async def factory(*, platform, account, evidence_role):
        return _collect_result(platform, account, mentions=[_mention()])
    cfg = _cfg(social_collection_interval_seconds=43200)
    svc = _svc(session_factory, cfg=cfg, factory=factory)
    svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST",
                      platform="instagram", handle="arijitsingh", now=NOW)
    wl = svc.watchlist()
    assert wl["active_identities"] == 1 and wl["eligible_now"] == 1
    r1 = await svc.run_once(now=NOW)
    assert r1["processed"] == 1
    with session_factory() as s:
        row = s.execute(select(SocialIdentity)).scalar_one()
    assert row.last_collected_at is not None and row.next_eligible_at is not None
    assert row.next_eligible_at > NOW_N and row.collection_state == "ELIGIBLE"
    # not eligible again at the same instant (scheduled forward by the interval)
    r2 = await svc.run_once(now=NOW)
    assert r2["processed"] == 0
    # eligible again once the interval elapses
    r3 = await svc.run_once(now=LATER)
    assert r3["processed"] == 1


# 8 + 9 — credential-unavailable / access-pending is honest; no unauthorized fallback
@pytest.mark.asyncio
async def test_access_pending_is_honest_no_fallback(session_factory):
    calls = {"n": 0}
    async def factory(*, platform, account, evidence_role):
        calls["n"] += 1
        return _collect_result(platform, account, access="CREDENTIAL_UNAVAILABLE",
                               collectible=False, mentions=[])
    svc = _svc(session_factory, factory=factory)
    svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST",
                      platform="instagram", handle="arijitsingh", now=NOW)
    r = await svc.run_once(now=NOW)
    assert r["deferred"] == 1 and r["failed"] == 0 and r["mentions_new"] == 0
    assert r["by_access_state"].get("CREDENTIAL_UNAVAILABLE") == 1
    with session_factory() as s:
        row = s.execute(select(SocialIdentity)).scalar_one()
        mentions = s.execute(select(SocialMention)).scalars().all()
    # honest access-pending state, backed off, NOT a failure; no evidence fabricated
    assert row.collection_state == "ACCESS_PENDING" and row.consecutive_failures == 0
    assert row.next_eligible_at > NOW_N and mentions == []


@pytest.mark.asyncio
async def test_transient_failure_does_not_corrupt_identity(session_factory):
    async def factory(*, platform, account, evidence_role):
        raise RuntimeError("network down")
    svc = _svc(session_factory, factory=factory)
    svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST",
                      platform="instagram", handle="arijitsingh", now=NOW)
    r = await svc.run_once(now=NOW)
    assert r["failed"] == 1 and r["collected"] == 0
    with session_factory() as s:
        row = s.execute(select(SocialIdentity)).scalar_one()
    assert row.active and row.consecutive_failures == 1 and row.collection_state == "DEFERRED"
    assert row.next_eligible_at > NOW_N  # backed off, identity intact
    assert row.canonical_entity_id == "artist:a"


# coverage read model
@pytest.mark.asyncio
async def test_coverage_read_model(session_factory):
    async def factory(*, platform, account, evidence_role):
        return _collect_result(platform, account, mentions=[_mention()])
    svc = _svc(session_factory, factory=factory)
    svc.link_identity(canonical_entity_id="artist:a", canonical_entity_type="ARTIST",
                      platform="instagram", handle="arijitsingh", now=NOW)
    await svc.run_once(now=NOW)
    cov = svc.coverage()
    assert cov["total_identities"] == 1 and cov["total_mentions"] == 1
    assert cov["by_platform"]["INSTAGRAM"]["mentions"] == 1
    assert cov["by_platform"]["INSTAGRAM"]["canonical_entities"] == 1
