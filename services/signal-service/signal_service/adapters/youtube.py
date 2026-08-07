"""YouTube Data API adapter — normalizes channel signals into observations.

Primary digital-popularity signal for the India-first profile (open API, cheap quota,
India's #1 music surface). Runs in mock mode when NQUARK_YOUTUBE_API_KEY is unset.
"""

from datetime import UTC, datetime
from typing import Any

import httpx

from signal_service.config import settings
from signal_service.schemas import NormalizedObservation, YouTubeChannelSignals

SIGNAL_SOURCE = "youtube"
ADAPTER_VERSION = "youtube-v1"
DEFAULT_MOCK_CHANNEL_ID = "UCq-Fj5jknLsUf-MWSy4_brA"  # T-Series

# Deterministic demo trending positions (India, music category) keyed by channel id.
MOCK_TRENDING_RANK_IN: dict[str, int] = {DEFAULT_MOCK_CHANNEL_ID: 2}


def entity_id_for_youtube_channel(channel_id: str) -> str:
    """Type-neutral source handle. The adapter does NOT assert an entity type — a channel
    may be an artist, a label, a promoter, or a media network. Classification decides the
    type before canonical resolution."""
    return f"youtube:channel:{channel_id}"


def _provenance(collected_at: datetime, *, source_url: str) -> dict[str, Any]:
    """Compliance envelope: official API, entity-level, no PII."""
    return {
        "acquisition_method": "official_api",
        "legal_basis": "platform_api_tos",
        "data_subject_type": "entity",
        "contains_pii": False,
        "adapter_version": ADAPTER_VERSION,
        "collected_at": collected_at.isoformat(),
        "source_url": source_url,
    }


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_channel_response(
    channel_id: str,
    channel: dict[str, Any],
    *,
    mock: bool = False,
    fetched_at: datetime | None = None,
    trending_rank: int | None = None,
) -> YouTubeChannelSignals:
    """Convert a YouTube channels.list item into normalized observation payloads."""
    when = fetched_at or datetime.now(UTC)
    entity = entity_id_for_youtube_channel(channel_id)
    snippet = channel.get("snippet") or {}
    statistics = channel.get("statistics") or {}

    name = snippet.get("title", "Unknown Channel")
    subscribers = _as_int(statistics.get("subscriberCount"))
    total_views = _as_int(statistics.get("viewCount"))
    videos = _as_int(statistics.get("videoCount"))
    channel_url = f"https://www.youtube.com/channel/{channel_id}"

    base_evidence = {
        "channel_id": channel_id,
        "channel_url": channel_url,
        "endpoint": "/youtube/v3/channels",
        "fetched_at": when.isoformat(),
    }
    provenance = _provenance(when, source_url=channel_url)
    base_metadata = {
        "adapter": ADAPTER_VERSION,
        "signal_provider": SIGNAL_SOURCE,
        "mock": mock,
        "provenance": provenance,
    }
    confidence = 0.5 if mock else 0.95

    observations: list[NormalizedObservation] = [
        NormalizedObservation(
            entity=entity,
            attribute="display_name",
            value=name,
            source=SIGNAL_SOURCE,
            timestamp=when,
            confidence=0.99 if not mock else 0.5,
            evidence={**base_evidence, "field": "snippet.title"},
            metadata={**base_metadata, "normalization": "direct"},
        ),
    ]

    if subscribers is not None:
        observations.append(
            NormalizedObservation(
                entity=entity,
                attribute="subscriber_count",
                value=subscribers,
                source=SIGNAL_SOURCE,
                timestamp=when,
                confidence=confidence,
                evidence={**base_evidence, "field": "statistics.subscriberCount"},
                metadata={**base_metadata, "normalization": "direct"},
            )
        )

    if total_views is not None:
        observations.append(
            NormalizedObservation(
                entity=entity,
                attribute="total_view_count",
                value=total_views,
                source=SIGNAL_SOURCE,
                timestamp=when,
                confidence=confidence,
                evidence={**base_evidence, "field": "statistics.viewCount"},
                metadata={**base_metadata, "normalization": "direct"},
            )
        )

    if videos is not None:
        observations.append(
            NormalizedObservation(
                entity=entity,
                attribute="video_count",
                value=videos,
                source=SIGNAL_SOURCE,
                timestamp=when,
                confidence=confidence,
                evidence={**base_evidence, "field": "statistics.videoCount"},
                metadata={**base_metadata, "normalization": "direct"},
            )
        )

    if trending_rank is not None:
        observations.append(
            NormalizedObservation(
                entity=entity,
                attribute="trending_rank_in",
                value=trending_rank,
                source=SIGNAL_SOURCE,
                timestamp=when,
                confidence=confidence,
                evidence={
                    **base_evidence,
                    "field": "videos.mostPopular",
                    "region_code": settings.youtube_region_code,
                },
                metadata={
                    **base_metadata,
                    "normalization": "ranked",
                    "region_code": settings.youtube_region_code,
                },
            )
        )

    return YouTubeChannelSignals(
        channel_id=channel_id,
        entity=entity,
        name=name,
        observations=observations,
        fetched_at=when,
        mock=mock,
    )


def mock_channel_payload(channel_id: str) -> dict[str, Any]:
    """Deterministic demo payload when a YouTube API key is unavailable."""
    catalog: dict[str, dict[str, Any]] = {
        DEFAULT_MOCK_CHANNEL_ID: {
            "id": DEFAULT_MOCK_CHANNEL_ID,
            "snippet": {"title": "T-Series", "country": "IN"},
            "statistics": {
                "subscriberCount": "289000000",
                "viewCount": "270000000000",
                "videoCount": "20500",
            },
        },
    }
    if channel_id in catalog:
        return catalog[channel_id]
    return {
        "id": channel_id,
        "snippet": {"title": f"Mock Channel ({channel_id[:8]})", "country": "IN"},
        "statistics": {
            "subscriberCount": "1000000",
            "viewCount": "500000000",
            "videoCount": "500",
        },
    }


# --------------------------------------------------------------------------- Phase 5A additions
# Additive, acquisition-only primitives the demand layer (artist-intelligence-service) needs:
# bounded channel SEARCH (identity discovery) and recent VIDEO stats (repeated observation by known
# id). Neither touches the existing ingest pipeline. Mock catalogs keep the whole flow demonstrable
# offline; the demand layer never talks to YouTube directly — it calls signal-service.
import re as _re
from datetime import datetime as _dt

from signal_service.schemas import (
    YouTubeSearchCandidate,
    YouTubeSearchResult,
    YouTubeVideoSignals,
    YouTubeVideoStat,
)


def _norm_name(value: str) -> str:
    return _re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


# Real Google channel ids so the demo reads truthfully; statistics/videos are illustrative and are
# clearly flagged mock via ``mock=true``. A confusable pair ("the local train") lets the demand
# layer's resolver return AMBIGUOUS rather than guess.
_MOCK_SEARCH: dict[str, list[dict[str, Any]]] = {
    "arijit singh": [
        {"channel_id": "UCUEcefFC0sBRZfCTBqcx9jg", "title": "Arijit Singh",
         "description": "Official channel of Arijit Singh. Music and live performances.",
         "handle": "@arijitsingh", "topic_signal": True},
    ],
    "diljit dosanjh": [
        {"channel_id": "UCT9zcQNlyht7fRlcjmflRSA", "title": "Diljit Dosanjh",
         "description": "Official YouTube channel of Diljit Dosanjh.",
         "handle": "@diljitdosanjh", "topic_signal": True},
    ],
    "nucleya": [
        {"channel_id": "UCnucleyaBassOfficial00", "title": "Nucleya",
         "description": "Bass Rani. Indian electronic music producer.",
         "handle": "@nucleya", "topic_signal": True},
    ],
    "the local train": [
        {"channel_id": "UCtlt_official_0000000", "title": "The Local Train",
         "description": "Official band channel.", "handle": "@thelocaltrain", "topic_signal": True},
        {"channel_id": "UCtlt_fanclub_00000000", "title": "The Local Train",
         "description": "Fan uploads and covers.", "handle": "@tlt_fans", "topic_signal": False},
    ],
}
_MOCK_UPLOADS: dict[str, str] = {
    "UCUEcefFC0sBRZfCTBqcx9jg": "UUUEcefFC0sBRZfCTBqcx9jg",
    "UCT9zcQNlyht7fRlcjmflRSA": "UUT9zcQNlyht7fRlcjmflRSA",
}
_MOCK_VIDEOS: dict[str, list[dict[str, Any]]] = {
    "UCUEcefFC0sBRZfCTBqcx9jg": [
        {"video_id": "arjt_v1", "title": "Arijit Singh — Live in Mumbai 2026",
         "published_at": "2026-07-20T12:00:00Z", "views": 4200000, "likes": 310000, "comments": 12000},
        {"video_id": "arjt_v2", "title": "Arijit Singh — Studio Session",
         "published_at": "2026-06-15T12:00:00Z", "views": 1800000, "likes": 150000, "comments": 5400},
    ],
    "UCT9zcQNlyht7fRlcjmflRSA": [
        {"video_id": "dlj_v1", "title": "Diljit Dosanjh — Dil-Luminati Tour",
         "published_at": "2026-07-01T12:00:00Z", "views": 9800000, "likes": 720000, "comments": 41000},
    ],
}


def _channel_url(channel_id: str) -> str:
    return f"https://www.youtube.com/channel/{channel_id}"


def _parse_ts(value: str | None) -> _dt | None:
    if not value:
        return None
    try:
        return _dt.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class YouTubeClient:
    async def search_channels(self, query: str, *, limit: int = 5) -> YouTubeSearchResult:
        """Bounded channel search for identity discovery (search.list; 100 quota units when live).

        Discovery only — the demand layer resolves identity from these candidates; it does not treat
        search as measurement."""
        when = datetime.now(UTC)
        if settings.use_youtube_mock:
            rows = _MOCK_SEARCH.get(_norm_name(query))
            if rows is None:
                # Unknown artist → a single weak, non-topic candidate (fails the resolver threshold).
                slug = _re.sub(r"[^a-z0-9]", "", _norm_name(query))[:16] or "unknown"
                rows = [{"channel_id": f"UCmock_{slug}", "title": query.title(),
                         "description": "Uploads.", "handle": None, "topic_signal": False}]
            candidates = [
                YouTubeSearchCandidate(channel_id=r["channel_id"], title=r["title"],
                                       description=r.get("description", ""), handle=r.get("handle"),
                                       canonical_url=_channel_url(r["channel_id"]),
                                       topic_signal=bool(r.get("topic_signal")))
                for r in rows[:limit]
            ]
            return YouTubeSearchResult(query=query, candidates=candidates, fetched_at=when, mock=True)

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{settings.youtube_api_base}/search",
                params={"part": "snippet", "q": query, "type": "channel",
                        "maxResults": min(limit, 25), "key": settings.youtube_api_key},
            )
            response.raise_for_status()
            items = response.json().get("items") or []
        candidates = []
        for item in items:
            snip = item.get("snippet") or {}
            cid = snip.get("channelId") or (item.get("id") or {}).get("channelId")
            if not cid:
                continue
            candidates.append(YouTubeSearchCandidate(
                channel_id=cid, title=snip.get("channelTitle") or snip.get("title", ""),
                description=snip.get("description", ""), handle=None,
                canonical_url=_channel_url(cid), topic_signal=False))
        return YouTubeSearchResult(query=query, candidates=candidates, fetched_at=when, mock=False)

    async def fetch_recent_videos(self, channel_id: str, *, limit: int = 5) -> YouTubeVideoSignals:
        """Recent uploaded-video stats by known channel id (channels.list -> playlistItems.list ->
        videos.list; ~3 quota units when live). Repeated observation uses known ids, never search."""
        when = datetime.now(UTC)
        if settings.use_youtube_mock:
            uploads = _MOCK_UPLOADS.get(channel_id)
            rows = _MOCK_VIDEOS.get(channel_id, [])[:limit]
            videos = [YouTubeVideoStat(video_id=r["video_id"], title=r.get("title"),
                                       published_at=_parse_ts(r.get("published_at")),
                                       views=r.get("views"), likes=r.get("likes"),
                                       comments=r.get("comments")) for r in rows]
            return YouTubeVideoSignals(channel_id=channel_id, uploads_playlist_id=uploads,
                                       videos=videos, fetched_at=when, mock=True)

        async with httpx.AsyncClient(timeout=15.0) as client:
            chan = await client.get(
                f"{settings.youtube_api_base}/channels",
                params={"part": "contentDetails", "id": channel_id, "key": settings.youtube_api_key})
            chan.raise_for_status()
            items = chan.json().get("items") or []
            if not items:
                raise ValueError(f"YouTube channel not found: {channel_id}")
            uploads = (((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {})
                       .get("uploads"))
            if not uploads:
                return YouTubeVideoSignals(channel_id=channel_id, uploads_playlist_id=None,
                                           videos=[], fetched_at=when, mock=False)
            pl = await client.get(
                f"{settings.youtube_api_base}/playlistItems",
                params={"part": "contentDetails", "playlistId": uploads,
                        "maxResults": min(limit, 50), "key": settings.youtube_api_key})
            pl.raise_for_status()
            ids = [((i.get("contentDetails") or {}).get("videoId")) for i in (pl.json().get("items") or [])]
            ids = [i for i in ids if i][:limit]
            videos = []
            if ids:
                vresp = await client.get(
                    f"{settings.youtube_api_base}/videos",
                    params={"part": "snippet,statistics", "id": ",".join(ids),
                            "key": settings.youtube_api_key})
                vresp.raise_for_status()
                for v in vresp.json().get("items") or []:
                    snip, st = v.get("snippet") or {}, v.get("statistics") or {}
                    videos.append(YouTubeVideoStat(
                        video_id=v.get("id"), title=snip.get("title"),
                        published_at=_parse_ts(snip.get("publishedAt")),
                        views=_as_int(st.get("viewCount")), likes=_as_int(st.get("likeCount")),
                        comments=_as_int(st.get("commentCount"))))
        return YouTubeVideoSignals(channel_id=channel_id, uploads_playlist_id=uploads,
                                   videos=videos, fetched_at=when, mock=False)

    async def fetch_channel(self, channel_id: str) -> YouTubeChannelSignals:
        if settings.use_youtube_mock:
            payload = mock_channel_payload(channel_id)
            return normalize_channel_response(
                channel_id,
                payload,
                mock=True,
                trending_rank=MOCK_TRENDING_RANK_IN.get(channel_id),
            )

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{settings.youtube_api_base}/channels",
                params={
                    "part": "snippet,statistics",
                    "id": channel_id,
                    "key": settings.youtube_api_key,
                },
            )
            response.raise_for_status()
            items = response.json().get("items") or []
            if not items:
                raise ValueError(f"YouTube channel not found: {channel_id}")
            return normalize_channel_response(channel_id, items[0], mock=False)
