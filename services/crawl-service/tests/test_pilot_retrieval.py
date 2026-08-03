from crawl_service.enrichment.pilot.retrieval import (
    BLOCKED_OR_CHALLENGE,
    INVALID_HTML,
    NO_METADATA_FOUND,
    NOT_FOUND,
    PAGE_NOT_VALID_EVENT,
    RATE_LIMITED,
    SOURCE_CHALLENGE,
    SOURCE_UNAVAILABLE,
    SUCCESS_HTML,
    SUCCESS_NO_RELEVANT_METADATA,
    TIMEOUT,
    VALID,
    classify_response,
    validate_event_page,
)

_OG = '<meta property="og:title" content="Free Folk Nite"><meta property="og:description" content="Aug 01, 2026, 8:00 PM Skinny Mos">'


def test_classify_http_outcomes():
    assert classify_response(404, "text/html", "x") == NOT_FOUND
    assert classify_response(429, "text/html", "x") == RATE_LIMITED
    assert classify_response(503, "text/html", "x") == SOURCE_UNAVAILABLE
    assert classify_response(None, None, "") == TIMEOUT
    assert classify_response(200, "application/json", "{}") == INVALID_HTML


def test_classify_challenge_and_error_page():
    assert classify_response(200, "text/html", "<html>Just a moment... cloudflare</html>") == BLOCKED_OR_CHALLENGE
    assert classify_response(200, "text/html", "<html><head><title>404 Not Found</title></head></html>") == INVALID_HTML


def test_classify_success_and_no_metadata():
    assert classify_response(200, "text/html", "<html>" + _OG + "</html>") == SUCCESS_HTML
    assert classify_response(200, "text/html", "<html>a real long page with lots of text but no og or jsonld " * 5 + "</html>") == SUCCESS_NO_RELEVANT_METADATA


def test_validate_event_page():
    body = "<html>" + _OG + "</html>"
    assert validate_event_page(body, expected_title="Free Folk Nite", slug="free-folk-nite-01082026") == (True, VALID)
    assert validate_event_page(body, expected_title="Totally Different Concert", slug="totally-different")[1] == PAGE_NOT_VALID_EVENT
    assert validate_event_page("<html>Just a moment cloudflare</html>", expected_title="x", slug="x")[1] == SOURCE_CHALLENGE
    assert validate_event_page("<html>no metadata here</html>", expected_title="x", slug="x")[1] == NO_METADATA_FOUND


def test_slug_word_match_validates():
    body = '<meta property="og:title" content="Free Folk Nite">'
    assert validate_event_page(body, expected_title=None, slug="free-folk-nite-01082026")[0] is True
