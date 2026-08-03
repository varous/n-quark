from datetime import UTC, datetime, timedelta

from crawl_service.enrichment.registry import (
    CANONICAL_ENTITY_RELATIONSHIP,
    JSON_LD,
    OPEN_GRAPH,
    SOURCE_API,
    VISIBLE_TEXT,
    Candidate,
)
from crawl_service.enrichment.resolver import (
    AUTO_RESOLVED,
    CANONICAL_RELATIONSHIP,
    CONFLICT,
    DIRECT_SOURCE,
    RESOLVED_DIRECT,
    REVIEW_CONFLICT,
    UNRESOLVED,
    resolve_field,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _c(field_name, value, source_type, conf=0.8, observed_at=NOW):
    return Candidate(field_name=field_name, candidate_value=value, source_type=source_type,
                     extraction_method="X", confidence=conf, observed_at=observed_at).normalize()


def test_direct_structured_field_beats_lower_authority() -> None:
    r = resolve_field("starts_at", [
        _c("starts_at", "2026-09-10T19:00:00", SOURCE_API, 0.9),
        _c("starts_at", "2026-01-01T00:00:00", VISIBLE_TEXT, 0.9),
    ])
    assert r.resolved_value == "2026-09-10T19:00:00" and r.resolution_method == DIRECT_SOURCE
    assert len(r.contradicting) == 1  # the visible-text value is contradicting, not chosen


def test_lower_authority_conflict_does_not_override() -> None:
    r = resolve_field("starts_at", [
        _c("starts_at", "2026-09-10T19:00:00", JSON_LD, 0.85),
        _c("starts_at", "2026-01-01T00:00:00", OPEN_GRAPH, 0.9),  # higher conf but lower authority
    ])
    assert r.resolved_value == "2026-09-10T19:00:00"


def test_same_family_agreement_is_modest_not_consensus() -> None:
    # JSON-LD + visible text agree, but both are the same Boshow family -> NOT consensus (Phase 2.2).
    r = resolve_field("starts_at", [
        _c("starts_at", "2026-09-10T19:00:00", JSON_LD, 0.8),
        _c("starts_at", "2026-09-10T19:00:00", VISIBLE_TEXT, 0.8),
    ])
    assert r.reason_code == RESOLVED_DIRECT and 0.8 < r.confidence <= 0.95


def test_equal_authority_conflict_is_flagged() -> None:
    r = resolve_field("starts_at", [
        _c("starts_at", "2026-09-10T19:00:00", JSON_LD, 0.85),
        _c("starts_at", "2026-09-11T19:00:00", JSON_LD, 0.85),
    ])
    assert r.reason_code == CONFLICT and r.review_status == REVIEW_CONFLICT and r.resolved_value is None


def test_stale_candidate_does_not_override_newer_same_authority() -> None:
    r = resolve_field("starts_at", [
        _c("starts_at", "2026-09-10T19:00:00", JSON_LD, 0.85, observed_at=NOW),
        _c("starts_at", "2026-01-01T00:00:00", JSON_LD, 0.85, observed_at=NOW - timedelta(days=5)),
    ])
    # both JSON_LD but different values -> equal-authority conflict (deterministic, not silent pick)
    assert r.reason_code == CONFLICT


def test_canonical_relationship_method() -> None:
    r = resolve_field("city", [_c("city", "Kolkata", CANONICAL_ENTITY_RELATIONSHIP, 0.8)])
    assert r.resolution_method == CANONICAL_RELATIONSHIP and r.review_status == AUTO_RESOLVED


def test_no_candidates_stays_unresolved() -> None:
    r = resolve_field("starts_at", [])
    assert r.resolution_method == UNRESOLVED and r.resolved_value is None


def test_low_confidence_needs_review() -> None:
    r = resolve_field("starts_at", [_c("starts_at", "2026-09-10T19:00:00", VISIBLE_TEXT, 0.3)])
    assert r.review_status == "NEEDS_REVIEW"


def test_resolver_deterministic() -> None:
    cands = [_c("starts_at", "2026-09-10T19:00:00", JSON_LD, 0.85)]
    assert resolve_field("starts_at", cands).resolved_value == resolve_field("starts_at", cands).resolved_value
