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


class YouTubeClient:
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
