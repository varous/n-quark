"""Governed social acquisition adapter (Phase 5C.1 — Social Evidence Foundation).

External social HTTP lives in signal-service (never a separate microservice). This adapter is the
*seam* for Instagram/Facebook (official Meta Graph access) and Reddit (access pending). It produces
**factual/semantic claims + minimal provenance** from a watched public account — never a raw-post
warehouse, never scraping, never login-wall/CAPTCHA circumvention, never collecting commenters,
followers or other individual-user datasets.

Retention doctrine (docs/data-doctrine.md): the source caption/media is **transient** — used only long
enough to extract durable claims and compute a content hash, then dropped. This adapter returns claims
+ provenance; it does not return, and the caller does not persist, the raw expressive content.

Access honesty: with no authorized token the adapter reports an ACCESS state (DISABLED /
CREDENTIAL_UNAVAILABLE / ACCESS_PENDING) and yields **no** mentions — it never fabricates a failure and
never substitutes a scrape. A deterministic offline fixture (mock) exists purely so the governed spine
is demonstrable without credentials; mock mentions are clearly marked ``mock: true``.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from signal_service.config import settings

PARSER_VERSION = "social-claim-extractor-1"

# Governed evidence roles (kept aligned with source-governance).
SOCIAL_DISCOVERY = "SOCIAL_DISCOVERY"
OFFICIAL_ACCOUNT_EVIDENCE = "OFFICIAL_ACCOUNT_EVIDENCE"
COMMUNITY_EVIDENCE = "COMMUNITY_EVIDENCE"

# Acquisition access states (Axis A — acquisition posture, honest).
ACCESS_PRODUCTION = "PRODUCTION"                  # authorized token present → real Graph reads
ACCESS_MOCK = "MOCK"                              # deterministic offline fixture (no real network)
ACCESS_CREDENTIAL_UNAVAILABLE = "CREDENTIAL_UNAVAILABLE"  # platform enabled but no token
ACCESS_PENDING = "ACCESS_PENDING"                # no approved access path (e.g. Reddit)
ACCESS_DISABLED = "DISABLED"                      # platform flag off
ACCESS_UNSUPPORTED = "UNSUPPORTED"               # unknown platform

INSTAGRAM = "INSTAGRAM"
FACEBOOK = "FACEBOOK"
REDDIT = "REDDIT"
_PLATFORMS = (INSTAGRAM, FACEBOOK, REDDIT)

# Governed source descriptors — acquisition posture kept separate from transformation posture
# (docs/source-governance.md, docs/data-doctrine.md). Disposition is reported honestly.
SOCIAL_SOURCE_DESCRIPTORS: dict[str, dict[str, Any]] = {
    "instagram": {
        "platform": INSTAGRAM, "source_role": OFFICIAL_ACCOUNT_EVIDENCE,
        "acquisition_mode": "OFFICIAL_META_GRAPH_API", "provenance_level": "OFFICIAL_ACCOUNT",
        "automation_posture": "AUTHORIZED_API_REQUIRED", "continuous_collection_allowed": False,
        "claim_authority": ["ANNOUNCEMENT", "SCHEDULE", "VENUE", "LINEUP", "TICKETING"],
        "raw_content_retention": "EPHEMERAL", "expressive_content_retention": False,
        "commercial_raw_export": False, "derived_embedding_basis": "CANONICAL",
        "provenance_retention": "REQUIRED",
        "policy_reference": "https://developers.facebook.com/docs/instagram-api/"},
    "facebook": {
        "platform": FACEBOOK, "source_role": OFFICIAL_ACCOUNT_EVIDENCE,
        "acquisition_mode": "OFFICIAL_META_GRAPH_API", "provenance_level": "OFFICIAL_PAGE",
        "automation_posture": "AUTHORIZED_API_REQUIRED", "continuous_collection_allowed": False,
        "claim_authority": ["ANNOUNCEMENT", "SCHEDULE", "VENUE", "LINEUP", "TICKETING", "ENTITY_TYPE"],
        "raw_content_retention": "EPHEMERAL", "expressive_content_retention": False,
        "commercial_raw_export": False, "derived_embedding_basis": "CANONICAL",
        "provenance_retention": "REQUIRED",
        "policy_reference": "https://developers.facebook.com/docs/pages-api/"},
    "reddit": {
        "platform": REDDIT, "source_role": COMMUNITY_EVIDENCE,
        "acquisition_mode": "OFFICIAL_API_PENDING", "provenance_level": "COMMUNITY",
        "automation_posture": "AUTHORIZED_API_REQUIRED", "continuous_collection_allowed": False,
        "claim_authority": ["COMMUNITY_RUMOUR"],
        "raw_content_retention": "EPHEMERAL", "expressive_content_retention": False,
        "commercial_raw_export": False, "derived_embedding_basis": "CANONICAL",
        "provenance_retention": "REQUIRED",
        "policy_reference": "https://www.reddit.com/wiki/api-terms"},
}


def _platform_key(platform: str) -> str | None:
    p = (platform or "").strip().lower()
    return p if p in SOCIAL_SOURCE_DESCRIPTORS else None


def access_state(platform: str) -> str:
    """Honest acquisition-access state for a platform. Never guesses that access exists."""
    key = _platform_key(platform)
    if key is None:
        return ACCESS_UNSUPPORTED
    if not settings.social_enabled:
        return ACCESS_DISABLED
    if key == "reddit":
        # No approved commercial access path yet — represented as pending, never scraped.
        return ACCESS_PENDING if settings.reddit_enabled else ACCESS_DISABLED
    enabled = settings.instagram_enabled if key == "instagram" else settings.facebook_enabled
    if not enabled:
        return ACCESS_DISABLED
    if settings.meta_mock_mode:
        return ACCESS_MOCK
    if not settings.meta_access_token:
        return ACCESS_CREDENTIAL_UNAVAILABLE
    return ACCESS_PRODUCTION


def descriptors() -> list[dict[str, Any]]:
    return [{"source": key, "access_state": access_state(key), **value}
            for key, value in SOCIAL_SOURCE_DESCRIPTORS.items()]


@dataclass
class SocialClaimMention:
    """One durable social-evidence record: claims + provenance, NO raw expressive content."""
    platform: str
    source_account: str
    platform_post_id: str
    post_url: str | None
    published_at: str | None
    observed_at: str
    evidence_role: str
    extracted_claims: dict[str, Any]
    linked_handles: list[str] = field(default_factory=list)
    confidence: float = 0.5
    parser_version: str = PARSER_VERSION
    content_hash: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    mock: bool = False


@dataclass
class SocialCollectionResult:
    platform: str
    account: str
    access_state: str
    collectible: bool
    reason: str
    mentions: list[SocialClaimMention] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_claims(raw_caption: str, *, hints: dict[str, Any]) -> dict[str, Any]:
    """Deterministic (no-AI) claim extraction from transient caption text. Returns a bounded factual
    dict — never the caption itself. 5C.1 keeps this intentionally minimal; 5C.2 will add the full
    deterministic classifier. The raw caption is consumed here and never returned/persisted."""
    lowered = raw_caption.lower()
    claims: dict[str, Any] = {}
    if hints.get("event_name"):
        claims["event_name"] = hints["event_name"]
    if hints.get("event_date"):
        claims["event_date"] = hints["event_date"]
    if hints.get("city"):
        claims["city"] = hints["city"]
    if hints.get("venue_name"):
        claims["venue_name"] = hints["venue_name"]
    if hints.get("ticket_url"):
        claims["ticket_url"] = hints["ticket_url"]
    # a couple of deterministic surface signals (5C.2 will replace with the real classifier)
    signals = [w for w in ("tickets", "live", "tour", "sold out", "cancelled", "postponed", "lineup")
               if w in lowered]
    if signals:
        claims["surface_signals"] = signals
    return claims


# ---- deterministic offline fixture (mock) --------------------------------------------------------
# Real, well-known public entities so the spine is demonstrable; captions here are TRANSIENT inputs to
# extraction and are never persisted (only the derived claims + hash survive).
_MOCK_POSTS: dict[str, list[dict[str, Any]]] = {
    "arijitsingh": [
        {"post_id": "IG_ARIJIT_1", "published_at": "2026-09-01T10:00:00+00:00",
         "caption": "Kolkata! Live in concert 20 Sep 2026 at Aquatica. Tickets live now.",
         "hints": {"event_name": "Arijit Singh Live", "event_date": "2026-09-20", "city": "Kolkata",
                   "venue_name": "Aquatica", "ticket_url": "https://example.org/t/arijit"}},
    ],
    "bacardinh7": [
        {"post_id": "IG_VENUE_1", "published_at": "2026-08-20T09:00:00+00:00",
         "caption": "This weekend at our rooftop — indie night lineup announced.",
         "hints": {"event_name": "Indie Night", "city": "Mumbai"}},
    ],
}


def _mock_mentions(platform: str, account: str, role: str, now: datetime) -> list[SocialClaimMention]:
    key = account.lstrip("@").lower()
    out: list[SocialClaimMention] = []
    for post in _MOCK_POSTS.get(key, []):
        caption = post["caption"]                       # transient — consumed, never persisted
        claims = _extract_claims(caption, hints=post["hints"])
        out.append(SocialClaimMention(
            platform=platform, source_account=account, platform_post_id=post["post_id"],
            post_url=f"https://example.org/{key}/{post['post_id']}",
            published_at=post["published_at"], observed_at=now.isoformat(), evidence_role=role,
            extracted_claims=claims, confidence=0.5, content_hash=_hash(caption),
            provenance={"acquisition_method": "META_GRAPH_MOCK", "platform": platform,
                        "account": account, "fetched_at": now.isoformat(),
                        "raw_content_retention": "EPHEMERAL"},
            mock=True))
    return out


async def collect(platform: str, *, account: str, evidence_role: str | None = None,
                  now: datetime | None = None) -> SocialCollectionResult:
    """Collect durable social claims for one watched public account. Honest about access; no scraping."""
    now = now or datetime.now(UTC)
    key = _platform_key(platform)
    plat = key.upper() if key else (platform or "").upper()
    state = access_state(platform)
    role = evidence_role or (SOCIAL_SOURCE_DESCRIPTORS.get(key or "", {}).get("source_role")
                             or SOCIAL_DISCOVERY)
    if state in (ACCESS_DISABLED, ACCESS_CREDENTIAL_UNAVAILABLE, ACCESS_PENDING, ACCESS_UNSUPPORTED):
        # Honest non-collection — NOT a failure, NOT a scrape fallback.
        return SocialCollectionResult(platform=plat, account=account, access_state=state,
                                      collectible=False, reason=state, mentions=[])
    if state == ACCESS_MOCK:
        return SocialCollectionResult(platform=plat, account=account, access_state=state,
                                      collectible=True, reason="MOCK_FIXTURE",
                                      mentions=_mock_mentions(plat, account, role, now))
    # ACCESS_PRODUCTION — real Meta Graph read. Only reached when an authorized token is configured;
    # not exercised in this increment (no credentials). The client below performs bounded official reads.
    mentions = await _MetaGraphClient().recent_account_claims(plat, account, role, now)
    return SocialCollectionResult(platform=plat, account=account, access_state=state,
                                  collectible=True, reason="PRODUCTION", mentions=mentions)


class _MetaGraphClient:
    """Bounded official Meta Graph client seam. Reads only public account content the token is
    authorized for, extracts claims, and discards raw captions/media. No user/follower/comment
    collection. Left unexercised without credentials (production path)."""

    async def recent_account_claims(self, platform: str, account: str, role: str,
                                    now: datetime) -> list[SocialClaimMention]:  # pragma: no cover
        import httpx
        base = settings.meta_graph_base
        token = settings.meta_access_token
        # NOTE: the exact edge/fields depend on the granted permissions; kept minimal + bounded.
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{base}/{account}/media",
                params={"fields": "id,permalink,timestamp,caption", "limit": 10, "access_token": token})
            resp.raise_for_status()
            data = resp.json().get("data", [])
        out: list[SocialClaimMention] = []
        for item in data:
            caption = item.get("caption") or ""              # transient
            claims = _extract_claims(caption, hints={})
            out.append(SocialClaimMention(
                platform=platform, source_account=account, platform_post_id=str(item.get("id")),
                post_url=item.get("permalink"), published_at=item.get("timestamp"),
                observed_at=now.isoformat(), evidence_role=role, extracted_claims=claims,
                confidence=0.6, content_hash=_hash(caption),
                provenance={"acquisition_method": "META_GRAPH_API", "platform": platform,
                            "account": account, "fetched_at": now.isoformat(),
                            "raw_content_retention": "EPHEMERAL"}))
        return out
