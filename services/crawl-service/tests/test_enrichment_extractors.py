from datetime import UTC, datetime

from crawl_service.enrichment.extractors import (
    candidates_from_jsonld,
    candidates_from_opengraph,
    candidates_from_visible_text,
    parse_jsonld_events,
    select_event,
)

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _jsonld(block: str) -> str:
    return f'<html><head><script type="application/ld+json">{block}</script></head></html>'


def test_single_event_candidates() -> None:
    html = _jsonld('{"@type":"Event","name":"Free Folk Nite","startDate":"2026-09-10T19:00:00",'
                   '"location":{"@type":"Place","name":"Skinny Mos","address":{"addressLocality":"Kolkata"}}}')
    cands = {c.field_name: c.normalized_value for c in candidates_from_jsonld(
        html, title="Free Folk Nite", source_record_id="free-folk-nite", source_url="u", observed_at=NOW)}
    assert cands["starts_at"] == "2026-09-10T19:00:00"
    assert cands["venue_name"] == "Skinny Mos" and cands["city"] == "Kolkata"


def test_graph_and_array_and_multi_select_correct_event() -> None:
    html = _jsonld('{"@graph":[{"@type":"WebPage"},'
                   '{"@type":"Event","name":"Other Show","startDate":"2026-01-01T00:00:00"},'
                   '{"@type":"MusicEvent","name":"Free Folk Nite","startDate":"2026-09-10T19:00:00"}]}')
    events = parse_jsonld_events(html)
    assert len(events) == 2  # WebPage excluded
    chosen = select_event(events, title="Free Folk Nite", source_record_id="free-folk-nite-2026")
    assert chosen["name"] == "Free Folk Nite"  # not the first Event


def test_missing_field_creates_no_candidate() -> None:
    html = _jsonld('{"@type":"Event","name":"X"}')  # no date/venue
    cands = candidates_from_jsonld(html, title="X", source_record_id="x", source_url="u", observed_at=NOW)
    assert all(c.field_name not in ("starts_at", "venue_name", "city") for c in cands)


def test_invalid_jsonld_is_safe() -> None:
    html = _jsonld("{not valid json")
    assert parse_jsonld_events(html) == []
    assert candidates_from_jsonld(html, title="x", source_record_id="x", source_url="u", observed_at=NOW) == []


def test_opengraph_candidates_lower_authority() -> None:
    html = '<meta property="og:event:start_time" content="2026-09-10T19:00:00">'
    cands = candidates_from_opengraph(html, source_url="u", observed_at=NOW)
    assert cands and cands[0].field_name == "starts_at" and cands[0].source_type == "OPEN_GRAPH"


def test_visible_text_labelled_fields() -> None:
    text = "Some intro. Venue: The Urban Theatre Project | City: Kolkata"
    cands = {c.field_name: c.normalized_value for c in candidates_from_visible_text(
        text, source_url="u", observed_at=NOW)}
    assert cands["venue_name"] == "The Urban Theatre Project" and cands["city"] == "Kolkata"


def test_unparseable_date_is_not_a_candidate() -> None:
    html = _jsonld('{"@type":"Event","name":"X","startDate":"sometime next week"}')
    cands = candidates_from_jsonld(html, title="X", source_record_id="x", source_url="u", observed_at=NOW)
    assert all(c.field_name != "starts_at" for c in cands)  # parse failure != null candidate
