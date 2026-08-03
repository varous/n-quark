"""Result classification + retry policy (Phase 2). Pure and deterministic.

Distinguishes a *failed request* from *successful evidence of absence* — a failure must never be
reported as record absence (Shadow Ledger disappearance depends on this).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Result codes for a capture attempt.
SUCCESS_RECORD_PRESENT = "SUCCESS_RECORD_PRESENT"
SUCCESS_RECORD_ABSENT = "SUCCESS_RECORD_ABSENT"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
RATE_LIMITED = "RATE_LIMITED"
TIMEOUT = "TIMEOUT"
PARSER_FAILED = "PARSER_FAILED"
INVALID_RESPONSE = "INVALID_RESPONSE"
TERMINAL_EVENT = "TERMINAL_EVENT"

_SUCCESS = frozenset({SUCCESS_RECORD_PRESENT, SUCCESS_RECORD_ABSENT})
_BACKOFF = frozenset({SOURCE_UNAVAILABLE, RATE_LIMITED, TIMEOUT})
_PARSER = frozenset({PARSER_FAILED, INVALID_RESPONSE})

# Map a capture result to the Shadow Ledger capture_status (only meaningful ones are submitted).
SHADOW_CAPTURE_STATUS = {
    SUCCESS_RECORD_ABSENT: "CAPTURE_SUCCESS_RECORD_ABSENT",
    SOURCE_UNAVAILABLE: "SOURCE_UNAVAILABLE",
    RATE_LIMITED: "SOURCE_UNAVAILABLE",
    TIMEOUT: "CAPTURE_FAILED",
    PARSER_FAILED: "PARSER_FAILED",
    INVALID_RESPONSE: "PARSER_FAILED",
}


@dataclass
class CaptureOutcome:
    result_code: str
    http_status: int | None = None
    retry_after_seconds: int | None = None
    shadow_result: dict[str, Any] | None = None  # signal-service's shadow_ledger response, if present
    canonical_event_id: str | None = None
    error: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetryDecision:
    is_success: bool
    should_retry: bool
    backoff_seconds: int | None
    terminal: bool
    needs_review: bool
    is_authoritative_absence: bool


def backoff_seconds(attempt: int, *, base: int, cap: int) -> int:
    """Exponential backoff with a cap. attempt is 1-based."""
    exp = base * (2 ** max(0, attempt - 1))
    return min(exp, cap)


def decide_retry(
    result_code: str,
    *,
    attempt: int,
    max_attempts: int,
    parser_retry_limit: int,
    retry_after_seconds: int | None,
    backoff_base: int,
    backoff_cap: int,
) -> RetryDecision:
    if result_code in _SUCCESS:
        return RetryDecision(
            is_success=True, should_retry=False, backoff_seconds=None, terminal=False,
            needs_review=False, is_authoritative_absence=(result_code == SUCCESS_RECORD_ABSENT),
        )
    if result_code == TERMINAL_EVENT:
        return RetryDecision(False, False, None, terminal=True, needs_review=False, is_authoritative_absence=False)

    if result_code in _PARSER:
        if attempt >= parser_retry_limit:
            # Give up to manual review rather than looping on a broken parser.
            return RetryDecision(False, False, None, terminal=True, needs_review=True, is_authoritative_absence=False)
        return RetryDecision(
            False, True, backoff_seconds(attempt, base=backoff_base, cap=backoff_cap),
            terminal=False, needs_review=False, is_authoritative_absence=False,
        )

    if result_code in _BACKOFF:
        if attempt >= max_attempts:
            return RetryDecision(False, False, None, terminal=True, needs_review=False, is_authoritative_absence=False)
        delay = retry_after_seconds if (result_code == RATE_LIMITED and retry_after_seconds) else \
            backoff_seconds(attempt, base=backoff_base, cap=backoff_cap)
        return RetryDecision(False, True, delay, terminal=False, needs_review=False, is_authoritative_absence=False)

    # Unknown -> treat as retryable source failure, bounded.
    if attempt >= max_attempts:
        return RetryDecision(False, False, None, terminal=True, needs_review=True, is_authoritative_absence=False)
    return RetryDecision(
        False, True, backoff_seconds(attempt, base=backoff_base, cap=backoff_cap),
        terminal=False, needs_review=False, is_authoritative_absence=False,
    )
