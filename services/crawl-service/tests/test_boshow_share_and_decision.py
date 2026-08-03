from datetime import UTC, datetime

from crawl_service.enrichment.extractors import candidates_from_boshow_share
from crawl_service.enrichment.pilot.decision import (
    DISABLE_LOW_VALUE,
    KEEP_AS_FALLBACK,
    PROMOTE_TO_STANDARD_ENRICHMENT,
    REQUIRES_SOURCE_FIX,
    DecisionThresholds,
    recommend,
)

NOW = datetime(2026, 8, 1, tzinfo=UTC)
_SHARE = ('<meta property="og:title" content="Free Folk Nite">'
          '<meta property="og:description" content="Aug 01, 2026, 8:00 PM Skinny Mos">')


def test_boshow_share_extracts_date_and_venue():
    cands = {c.field_name: c.normalized_value for c in candidates_from_boshow_share(
        _SHARE, source_url="u", observed_at=NOW)}
    assert cands["starts_at"] == "2026-08-01T20:00:00"
    assert cands["venue_name"] == "Skinny Mos"


def test_boshow_share_malformed_description_no_candidate():
    html = '<meta property="og:description" content="Come to our great show!">'
    assert candidates_from_boshow_share(html, source_url="u", observed_at=NOW) == []


TH = DecisionThresholds()


def test_decision_disable_when_all_duplicate():
    m = {"pages_attempted": 10, "valid_event_pages": 10, "fields_evaluated": 20,
         "incremental_field_gain_count": 0, "conflict_count": 0, "freshness_gain_count": 0, "parser_failures": 0}
    assert recommend(m, TH)["recommendation"] == DISABLE_LOW_VALUE


def test_decision_keep_as_fallback_with_some_gain():
    m = {"pages_attempted": 10, "valid_event_pages": 10, "fields_evaluated": 40,
         "incremental_field_gain_count": 2, "conflict_count": 0, "freshness_gain_count": 1, "parser_failures": 0}
    assert recommend(m, TH)["recommendation"] == KEEP_AS_FALLBACK  # 2/40 = 0.05 < 0.1


def test_decision_promote_with_strong_gain():
    m = {"pages_attempted": 10, "valid_event_pages": 10, "fields_evaluated": 20,
         "incremental_field_gain_count": 6, "conflict_count": 1, "freshness_gain_count": 0, "parser_failures": 0}
    assert recommend(m, TH)["recommendation"] == PROMOTE_TO_STANDARD_ENRICHMENT


def test_decision_requires_source_fix_on_poor_retrieval():
    m = {"pages_attempted": 10, "valid_event_pages": 3, "fields_evaluated": 6,
         "incremental_field_gain_count": 3, "conflict_count": 0, "freshness_gain_count": 0, "parser_failures": 0}
    assert recommend(m, TH)["recommendation"] == REQUIRES_SOURCE_FIX
