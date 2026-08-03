from crawl_service.classification import (
    INVALID_RESPONSE,
    PARSER_FAILED,
    RATE_LIMITED,
    SOURCE_UNAVAILABLE,
    SUCCESS_RECORD_ABSENT,
    SUCCESS_RECORD_PRESENT,
    TERMINAL_EVENT,
    TIMEOUT,
    backoff_seconds,
    decide_retry,
)

CFG = {"max_attempts": 6, "parser_retry_limit": 2, "backoff_base": 120, "backoff_cap": 21600}


def _d(code, attempt=1, retry_after=None):
    return decide_retry(code, attempt=attempt, retry_after_seconds=retry_after, **CFG)


def test_success_present_and_absent_are_success_not_retried():
    p = _d(SUCCESS_RECORD_PRESENT)
    a = _d(SUCCESS_RECORD_ABSENT)
    assert p.is_success and not p.should_retry and not p.is_authoritative_absence
    assert a.is_success and not a.should_retry and a.is_authoritative_absence


def test_exponential_backoff():
    assert backoff_seconds(1, base=120, cap=21600) == 120
    assert backoff_seconds(2, base=120, cap=21600) == 240
    assert backoff_seconds(3, base=120, cap=21600) == 480
    assert backoff_seconds(20, base=120, cap=21600) == 21600  # capped


def test_timeout_and_source_unavailable_retry_then_terminal():
    d = _d(TIMEOUT, attempt=1)
    assert d.should_retry and d.backoff_seconds == 120 and not d.terminal
    end = _d(SOURCE_UNAVAILABLE, attempt=6)
    assert not end.should_retry and end.terminal


def test_rate_limited_respects_retry_after():
    d = _d(RATE_LIMITED, attempt=1, retry_after=42)
    assert d.should_retry and d.backoff_seconds == 42


def test_parser_failed_limited_then_manual_review():
    first = _d(PARSER_FAILED, attempt=1)
    assert first.should_retry and not first.terminal
    give_up = _d(INVALID_RESPONSE, attempt=2)
    assert give_up.terminal and give_up.needs_review and not give_up.should_retry


def test_terminal_event_stops():
    d = _d(TERMINAL_EVENT)
    assert d.terminal and not d.should_retry


def test_failures_never_marked_as_absence():
    for code in (TIMEOUT, SOURCE_UNAVAILABLE, RATE_LIMITED, PARSER_FAILED, INVALID_RESPONSE):
        assert _d(code).is_authoritative_absence is False
