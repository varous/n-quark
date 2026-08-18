"""Governed social acquisition adapter (Phase 5C.1) — signal-service.

Access posture is honest (DISABLED / CREDENTIAL_UNAVAILABLE / ACCESS_PENDING / MOCK / PRODUCTION);
there is no scraping fallback; the deterministic mock yields claims + provenance with the raw caption
consumed transiently and never returned.
"""
import asyncio

import pytest

from signal_service.adapters import social as S
from signal_service.config import settings


@pytest.fixture(autouse=True)
def _reset():
    keep = {k: getattr(settings, k) for k in
            ("social_enabled", "instagram_enabled", "facebook_enabled", "reddit_enabled",
             "meta_mock_mode", "meta_access_token")}
    yield
    for k, v in keep.items():
        setattr(settings, k, v)


def _enable_mock():
    settings.social_enabled = True
    settings.instagram_enabled = True
    settings.facebook_enabled = True
    settings.meta_mock_mode = True


def test_access_state_disabled_when_off():
    settings.social_enabled = False
    assert S.access_state("instagram") == S.ACCESS_DISABLED


def test_access_state_credential_unavailable():
    settings.social_enabled = True
    settings.instagram_enabled = True
    settings.meta_mock_mode = False
    settings.meta_access_token = ""
    assert S.access_state("instagram") == S.ACCESS_CREDENTIAL_UNAVAILABLE


def test_access_state_reddit_pending_not_scraped():
    settings.social_enabled = True
    settings.reddit_enabled = True
    assert S.access_state("reddit") == S.ACCESS_PENDING       # never PRODUCTION/scrape


def test_access_state_mock_and_unsupported():
    _enable_mock()
    assert S.access_state("instagram") == S.ACCESS_MOCK
    assert S.access_state("tiktok") == S.ACCESS_UNSUPPORTED


def test_descriptors_report_governed_roles_and_state():
    _enable_mock()
    rows = {d["source"]: d for d in S.descriptors()}
    assert rows["instagram"]["source_role"] == S.OFFICIAL_ACCOUNT_EVIDENCE
    assert rows["reddit"]["source_role"] == S.COMMUNITY_EVIDENCE
    # transformation posture present + honest access state
    assert rows["instagram"]["raw_content_retention"] == "EPHEMERAL"
    assert rows["instagram"]["expressive_content_retention"] is False
    assert rows["instagram"]["access_state"] == S.ACCESS_MOCK


def test_collect_deferred_when_no_access():
    settings.social_enabled = True
    settings.instagram_enabled = True
    settings.meta_mock_mode = False
    settings.meta_access_token = ""
    res = asyncio.run(S.collect("instagram", account="arijitsingh"))
    assert res.collectible is False and res.access_state == S.ACCESS_CREDENTIAL_UNAVAILABLE
    assert res.mentions == []                                  # no fabricated evidence, no scrape


def test_collect_mock_yields_claims_without_raw_caption():
    _enable_mock()
    res = asyncio.run(S.collect("instagram", account="arijitsingh"))
    assert res.collectible and res.access_state == S.ACCESS_MOCK and res.mentions
    m = res.mentions[0]
    assert m.extracted_claims.get("event_name") == "Arijit Singh Live"
    assert m.content_hash and m.mock is True
    # the raw caption is consumed transiently — it is not present on the returned evidence
    caption = "Kolkata! Live in concert 20 Sep 2026 at Aquatica. Tickets live now."
    assert caption not in str(m.__dict__)
    assert not hasattr(m, "caption") and not hasattr(m, "raw_text")


def test_collect_unknown_account_returns_no_mentions():
    _enable_mock()
    res = asyncio.run(S.collect("instagram", account="nobody-unknown"))
    assert res.collectible and res.mentions == []             # no fixture → honestly empty


# ---- 5C.2 enriched, bounded, deterministic extraction --------------------------------------------

def test_extract_signals_multi_label_and_negation():
    claims = S._extract_claims(
        "Live in concert! Tickets on sale now. Note: 12 Oct show will not take place.",
        hints={"event_name": "X", "event_date": "2026-10-12"})
    assert claims["signals"].get("announcement") and claims["signals"].get("ticketing")
    assert claims["signals"].get("cancellation") and claims.get("negation") is True
    # legacy flat list retained for backward compatibility
    assert set(claims["surface_signals"]) >= {"announcement", "ticketing", "cancellation"}


def test_extract_venue_change_old_new_is_bounded():
    claims = S._extract_claims(
        "Change of venue: moved from Gymkhana Grounds to NSCI Dome. See you there!",
        hints={"venue_name": "NSCI Dome"})
    assert claims["signals"].get("venue_change")
    assert claims["changes"]["venue"]["from"] and claims["changes"]["venue"]["to"]
    # every kept span is clipped — never the whole caption
    assert all(len(v) <= 80 for v in claims["changes"]["venue"].values())


def test_extract_uncertainty_teaser():
    claims = S._extract_claims("Something big is coming soon... stay tuned?", hints={})
    assert claims.get("uncertainty") is True and "signals" not in claims  # no event signal fired


def test_extract_never_returns_caption():
    caption = "Kolkata! Live in concert 20 Sep 2026 at Aquatica. Tickets live now."
    claims = S._extract_claims(caption, hints={"event_name": "Arijit Singh Live"})
    assert caption not in str(claims) and "coming soon" not in str(claims)
