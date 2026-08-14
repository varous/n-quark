"""YouTube provider (Phase 5A) — backed by signal-service, not a direct YouTube client.

Acquisition (search / channel / videos) is delegated to signal-service so there is one ingestion path
and the API key stays there. This provider adds the demand-layer semantics on top: deterministic
identity resolution (search discovery → authoritative channels.list verification), normalized
``DemandDatum`` output, and nominal quota metering driven by which primitive it invoked — under the
current model search.list is 1 unit in an INDEPENDENT Search-Queries quota, general reads are 1 unit.
"""

from __future__ import annotations

import re
from typing import Any

from artist_intelligence_service.providers.base import (
    AMBIGUOUS,
    CAP_CONTENT_SNAPSHOT,
    CAP_GLOBAL_SNAPSHOT,
    CAP_METADATA,
    CAP_RESOLVE,
    CAP_SEARCH,
    DIRECT_PROVIDER_VALUE,
    PROVIDER_REPORTED,
    PROVIDER_YOUTUBE,
    RESOLVED,
    UNRESOLVED,
    YT_CHANNEL_VIEWS,
    YT_SUBSCRIBERS,
    YT_VIDEO_COMMENTS,
    YT_VIDEO_COUNT,
    YT_VIDEO_LIKES,
    YT_VIDEO_VIEWS,
    ArtistCandidate,
    ArtistIntelligenceProvider,
    ChannelNotFound,
    DemandDatum,
    IdentityResolution,
)
from artist_intelligence_service.quota import QuotaMeter
from artist_intelligence_service.signal_client import SignalClient, channel_stat

ADAPTER_VERSION = "youtube-p5a-signal"

# Deterministic resolution thresholds (transparent + configurable via constants).
RESOLVE_THRESHOLD = 0.70   # top candidate must clear this to auto-resolve
RESOLVE_MARGIN = 0.20      # ...and beat the runner-up by at least this
AMBIGUOUS_FLOOR = 0.40     # below this, nothing is a plausible identity → UNRESOLVED


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_ts(value: Any):
    from datetime import datetime
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


class YouTubeProvider(ArtistIntelligenceProvider):
    name = PROVIDER_YOUTUBE
    capabilities = frozenset({CAP_SEARCH, CAP_RESOLVE, CAP_METADATA,
                              CAP_GLOBAL_SNAPSHOT, CAP_CONTENT_SNAPSHOT})

    def __init__(self, *, signal: SignalClient | None = None, meter: QuotaMeter | None = None) -> None:
        self.signal = signal or SignalClient()
        self.meter = meter or QuotaMeter()

    def _provenance(self, endpoint: str, mock: bool, extra: dict[str, Any] | None = None) -> dict:
        return {"provider": PROVIDER_YOUTUBE, "acquisition_method": "signal_service",
                "upstream_endpoint": endpoint, "adapter_version": ADAPTER_VERSION,
                "mock": mock, **(extra or {})}

    # ---- search / identity -------------------------------------------------------------------
    async def search_artist(self, query: str, *, limit: int,
                            purpose: str | None = None) -> list[ArtistCandidate]:
        self._require(CAP_SEARCH)
        try:
            payload = await self.signal.youtube_search(query, limit=limit)
            self.meter.search(ok=True, purpose=purpose)
        except Exception:
            self.meter.search(ok=False, purpose=purpose)
            raise
        out = []
        for r in payload.get("candidates") or []:
            out.append(ArtistCandidate(
                provider_id=r["channel_id"], identity_type="CHANNEL_ID",
                display_name=r.get("title", ""), canonical_url=r.get("canonical_url"),
                evidence={"title": r.get("title"), "description": (r.get("description") or "")[:280],
                          "handle": r.get("handle"), "topic_signal": bool(r.get("topic_signal"))}))
        return out

    async def resolve_identity(self, query: str, *, hints: dict, limit: int) -> IdentityResolution:
        self._require(CAP_RESOLVE)
        candidates = await self.search_artist(query, limit=limit)
        for c in candidates:
            c.score, c.signals = _score_candidate(query, c, hints or {})
        candidates.sort(key=lambda c: c.score, reverse=True)
        return _decide(candidates)

    # ---- metadata + snapshots (known ids → 1-unit reads) -------------------------------------
    async def get_artist_metadata(self, provider_id: str) -> dict:
        self._require(CAP_METADATA)
        signals = await self.signal.youtube_channel(provider_id)
        self.meter.read(units=1)
        return {"channel_id": provider_id, "title": channel_stat(signals, "display_name"),
                "statistics": {"subscriberCount": channel_stat(signals, "subscriber_count"),
                               "viewCount": channel_stat(signals, "total_view_count"),
                               "videoCount": channel_stat(signals, "video_count")},
                "mock": bool(signals.get("mock"))}

    async def verify_channel(self, provider_id: str) -> dict[str, Any]:
        """Authoritative channels.list existence check (Phase 5A.1a). Returns the signal-service
        verification dict ({status: FOUND|CHANNEL_NOT_FOUND, ...}); meters the 1-unit read. A
        provider/network failure raises (SignalAcquisitionError) — never a false NOT_FOUND."""
        self._require(CAP_METADATA)
        try:
            result = await self.signal.youtube_verify(provider_id)
            self.meter.read(units=1, ok=True)
            return result
        except Exception:
            self.meter.record("read", 0, ok=False)   # attempted verify that could not complete
            raise

    def channel_demand_from_verification(self, provider_id: str,
                                         verification: dict[str, Any]) -> list[DemandDatum]:
        """Build channel DemandData from an already-fetched (verified) channels.list result — no extra
        quota. Views/video-count are DIRECT_PROVIDER_VALUE; subscribers are PROVIDER_REPORTED (rounded)."""
        mock = bool(verification.get("mock"))
        prov = self._provenance("channels.list", mock, {"channel_id": provider_id})
        out: list[DemandDatum] = []
        views = _as_int(verification.get("total_view_count"))
        subs = _as_int(verification.get("subscriber_count"))
        videos = _as_int(verification.get("video_count"))
        if views is not None:
            out.append(DemandDatum(metric=YT_CHANNEL_VIEWS, value_numeric=views, unit="views",
                                   evidence_status=DIRECT_PROVIDER_VALUE, provenance=prov))
        if subs is not None:
            # YouTube publicly rounds subscriber counts to 3 significant figures — reported, not exact.
            out.append(DemandDatum(metric=YT_SUBSCRIBERS, value_numeric=subs, unit="subscribers",
                                   evidence_status=PROVIDER_REPORTED,
                                   provenance={**prov, "precision": "rounded_3sf"}))
        if videos is not None:
            out.append(DemandDatum(metric=YT_VIDEO_COUNT, value_numeric=videos, unit="videos",
                                   evidence_status=DIRECT_PROVIDER_VALUE, provenance=prov))
        return out

    async def get_global_snapshot(self, provider_id: str) -> list[DemandDatum]:
        self._require(CAP_GLOBAL_SNAPSHOT)
        # channels.list IS the authoritative check: an empty result is a definitive NOT_FOUND, so we
        # never fabricate zero statistics for a channel that no longer exists.
        verification = await self.verify_channel(provider_id)
        if verification.get("status") != "FOUND":
            raise ChannelNotFound(provider_id)
        return self.channel_demand_from_verification(provider_id, verification)

    async def list_uploads(self, provider_id: str, *, limit: int) -> dict[str, Any]:
        """Raw uploads (video metadata) for the catalogue registry (Phase 5A.3). Meters a bounded
        general read (channels.list + playlistItems.list + videos.list ≈ 3 units). Discovers via the
        official uploads playlist, never search."""
        payload = await self.signal.youtube_videos(provider_id, limit=limit)
        self.meter.read(units=3)
        return {"videos": payload.get("videos") or [], "mock": bool(payload.get("mock"))}

    async def get_video_stats_batch(self, video_ids: list[str]) -> list[DemandDatum]:
        """Batch video statistics for known ids via signal-service videos.list (Phase 5A.3). Quota-
        efficient (VIDEO_STATS_BATCH bucket, ~1 unit / 50 ids) for high-volume known-video refreshes."""
        payload = await self.signal.youtube_videos_batch(video_ids)
        self.meter.video_batch(units=1)
        mock = bool(payload.get("mock"))
        out: list[DemandDatum] = []
        for v in payload.get("videos") or []:
            vid = v.get("video_id")
            if not vid:
                continue
            ts = _parse_ts(v.get("published_at"))
            prov = self._provenance("videos.list:batch", mock,
                                    {"video_id": vid, "published_at": v.get("published_at"),
                                     "title": v.get("title")})
            for metric, key in ((YT_VIDEO_VIEWS, "views"), (YT_VIDEO_LIKES, "likes"),
                                (YT_VIDEO_COMMENTS, "comments")):
                val = _as_int(v.get(key))
                if val is not None:
                    out.append(DemandDatum(metric=metric, value_numeric=val, unit="count",
                                           scope_type="CONTENT", scope_id=vid,
                                           scope_label=v.get("title"), provider_timestamp=ts,
                                           evidence_status=DIRECT_PROVIDER_VALUE,
                                           dedup_extra=vid, provenance=prov))
        return out

    async def get_content_snapshot(self, provider_id: str, *, limit: int) -> list[DemandDatum]:
        self._require(CAP_CONTENT_SNAPSHOT)
        payload = await self.signal.youtube_videos(provider_id, limit=limit)
        # nominal cost: channels.list + playlistItems.list + videos.list ≈ 3 read units
        self.meter.read(units=3)
        mock = bool(payload.get("mock"))
        out: list[DemandDatum] = []
        for v in payload.get("videos") or []:
            vid = v.get("video_id")
            if not vid:
                continue
            ts = _parse_ts(v.get("published_at"))
            prov = self._provenance("videos.list", mock,
                                    {"channel_id": provider_id, "video_id": vid,
                                     "published_at": v.get("published_at"), "title": v.get("title")})
            for metric, key in ((YT_VIDEO_VIEWS, "views"), (YT_VIDEO_LIKES, "likes"),
                                (YT_VIDEO_COMMENTS, "comments")):
                val = _as_int(v.get(key))
                if val is not None:
                    out.append(DemandDatum(metric=metric, value_numeric=val, unit="count",
                                           scope_type="CONTENT", scope_id=vid,
                                           scope_label=v.get("title"), provider_timestamp=ts,
                                           evidence_status=DIRECT_PROVIDER_VALUE,
                                           dedup_extra=vid, provenance=prov))
        return out


# --------------------------------------------------------------- deterministic scoring + decision
def _score_candidate(query: str, cand: ArtistCandidate, hints: dict) -> tuple[float, list[str]]:
    """Transparent additive evidence. Name equality alone is deliberately NOT sufficient to resolve —
    a topic/official or corroborating handle/url/known-id signal is needed to clear the threshold."""
    ev = cand.evidence or {}
    q, title = _norm(query), _norm(ev.get("title") or cand.display_name)
    score, signals = 0.0, []
    if q and q == title:
        score += 0.5
        signals.append("exact_name_match")
    elif q and (q in title or title in q):
        score += 0.25
        signals.append("partial_name_match")
    if ev.get("topic_signal"):
        score += 0.3
        signals.append("music_topic_signal")
    known_handles = {str(h).lower().lstrip("@") for h in (hints.get("known_handles") or [])}
    handle = str(ev.get("handle") or "").lower().lstrip("@")
    if handle and handle in known_handles:
        score += 0.4
        signals.append("known_handle_match")
    known_urls = {str(u).lower() for u in (hints.get("known_urls") or [])}
    if cand.canonical_url and cand.canonical_url.lower() in known_urls:
        score += 0.4
        signals.append("known_url_match")
    if str(hints.get("provider_id") or "") == cand.provider_id:
        score += 1.0
        signals.append("explicit_channel_id")
    return round(min(score, 1.0), 4), signals


def _looks_music_channel(verification: dict[str, Any]) -> bool:
    """Best-effort music-topic signal from authoritative channels.list metadata (topic categories or
    a music-suggestive description). Only used as ADDITIONAL corroboration, never as certainty."""
    topics = verification.get("topic_categories") or verification.get("topic_ids") or []
    if any("music" in str(t).lower() for t in topics):
        return True
    desc = str(verification.get("description") or "").lower()
    return any(w in desc for w in ("official artist channel", "official music", "musician", "singer", "vevo"))


def score_with_verification(query: str, cand: ArtistCandidate, verification: dict[str, Any],
                            hints: dict) -> tuple[float, list[str]]:
    """Re-score a candidate using AUTHORITATIVE channels.list metadata (5B.2.7 §8).

    Overlays the verified title/handle/topic onto the search-time evidence, re-runs the transparent
    additive score, and — when the authoritative channel title exactly matches the query — adds a
    bounded verification bonus. This is stronger evidence than a search snippet, but the clear-leader
    margin in ``_decide`` still prevents an equally-named impostor from resolving."""
    from artist_intelligence_service.config import settings
    ev = dict(cand.evidence or {})
    ev["title"] = verification.get("title") or ev.get("title")
    if verification.get("handle"):
        ev["handle"] = verification.get("handle")
    if not ev.get("topic_signal") and _looks_music_channel(verification):
        ev["topic_signal"] = True
    enriched = ArtistCandidate(provider_id=cand.provider_id, identity_type=cand.identity_type,
                               display_name=ev.get("title") or cand.display_name,
                               canonical_url=cand.canonical_url, evidence=ev)
    score, signals = _score_candidate(query, enriched, hints or {})
    if "exact_name_match" in signals:
        score = round(min(score + settings.youtube_authoritative_match_bonus, 1.0), 4)
        signals = [*signals, "authoritative_title_verified"]
    return score, signals


def _decide(candidates: list[ArtistCandidate]) -> IdentityResolution:
    method = "deterministic-evidence-v1"
    if not candidates:
        return IdentityResolution(status=UNRESOLVED, method=method,
                                  reason="no_candidates", candidates=[])
    top = candidates[0]
    if top.score < AMBIGUOUS_FLOOR:
        return IdentityResolution(status=UNRESOLVED, method=method,
                                  reason="best_candidate_below_floor", candidates=candidates)
    runner = candidates[1].score if len(candidates) > 1 else 0.0
    # round the margin so float subtraction (e.g. 1.0 - 0.8 = 0.19999…) can't drop a clear leader
    if top.score >= RESOLVE_THRESHOLD and round(top.score - runner, 6) >= RESOLVE_MARGIN:
        return IdentityResolution(status=RESOLVED, method=method, reason="clear_leader",
                                  chosen=top, candidates=candidates)
    return IdentityResolution(status=AMBIGUOUS, method=method,
                              reason="insufficient_margin", candidates=candidates)
