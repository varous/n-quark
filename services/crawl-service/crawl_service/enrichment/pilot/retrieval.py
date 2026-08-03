"""Live public-page retrieval classification + validation (Phase 2.2). Pure, deterministic.

Classifies an HTTP response and validates that the body is plausibly the intended event page before
any extraction. A generic error/challenge page must never be treated as valid event HTML.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# retrieval result classes
SUCCESS_HTML = "SUCCESS_HTML"
SUCCESS_NO_RELEVANT_METADATA = "SUCCESS_NO_RELEVANT_METADATA"
NOT_FOUND = "NOT_FOUND"
RATE_LIMITED = "RATE_LIMITED"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
TIMEOUT = "TIMEOUT"
INVALID_HTML = "INVALID_HTML"
PARSER_FAILED = "PARSER_FAILED"
BLOCKED_OR_CHALLENGE = "BLOCKED_OR_CHALLENGE"

# validation reason codes
VALID = "VALID"
PAGE_NOT_VALID_EVENT = "PAGE_NOT_VALID_EVENT"
SOURCE_CHALLENGE = "SOURCE_CHALLENGE"
NO_METADATA_FOUND = "NO_METADATA_FOUND"

_MIN_BODY = 120
_CHALLENGE_MARKERS = (
    "just a moment", "cf-challenge", "captcha", "access denied", "attention required",
    "cloudflare", "verify you are human", "enable javascript and cookies",
)
_ERROR_MARKERS = ("404 not found", "500 internal server error", "502 bad gateway", "nginx error")


def _has_metadata(body: str) -> bool:
    b = (body or "").lower()
    return ('property="og:' in b) or ("application/ld+json" in b)


def classify_response(status_code: int | None, content_type: str | None, body: str) -> str:
    if status_code is None:
        return TIMEOUT
    if status_code == 404:
        return NOT_FOUND
    if status_code == 429:
        return RATE_LIMITED
    if 500 <= status_code < 600:
        return SOURCE_UNAVAILABLE
    if status_code != 200:
        return SOURCE_UNAVAILABLE
    ct = (content_type or "").lower()
    if ct and "html" not in ct and "xml" not in ct:
        return INVALID_HTML
    low = (body or "").lower()
    if any(m in low for m in _CHALLENGE_MARKERS):
        return BLOCKED_OR_CHALLENGE
    if any(m in low for m in _ERROR_MARKERS) and not _has_metadata(body):
        return INVALID_HTML  # a 200 that is actually an error page
    if not _has_metadata(body):
        return SUCCESS_NO_RELEVANT_METADATA
    return SUCCESS_HTML


def _title(body: str) -> str:
    m = re.search(r'property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', body or "", re.IGNORECASE)
    return m.group(1).strip() if m else ""


def validate_event_page(body: str, *, expected_title: str | None, slug: str | None) -> tuple[bool, str]:
    """Deterministically decide whether the body is the intended event page."""
    low = (body or "").lower()
    if any(m in low for m in _CHALLENGE_MARKERS):
        return False, SOURCE_CHALLENGE
    if not _has_metadata(body):
        return False, NO_METADATA_FOUND
    title = _title(body)
    # Accept if the OG title matches the known title, or the slug's words appear in the title.
    if expected_title and title and SequenceMatcher(None, title.lower(), expected_title.lower()).ratio() >= 0.6:
        return True, VALID
    if slug:
        slug_words = [w for w in re.split(r"[^a-z0-9]+", slug.lower()) if len(w) > 2 and not w.isdigit()]
        if slug_words and title and sum(w in title.lower() for w in slug_words) >= max(1, len(slug_words) // 2):
            return True, VALID
    # Metadata present but no title match -> ambiguous; accept only if we have neither title nor slug.
    if not expected_title and not slug and title:
        return True, VALID
    return False, PAGE_NOT_VALID_EVENT
