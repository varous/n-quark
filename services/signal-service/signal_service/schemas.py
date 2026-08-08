from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class NormalizedObservation(BaseModel):
    """Signal normalized into an append-only observation payload."""

    entity: str
    attribute: str
    value: Any
    source: str = "spotify"
    timestamp: datetime | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = self.model_dump(exclude_none=True)
        if self.timestamp is not None:
            payload["timestamp"] = self.timestamp.isoformat()
        return payload


class SpotifyArtistSignals(BaseModel):
    spotify_id: str
    entity: str
    name: str
    observations: list[NormalizedObservation]
    fetched_at: datetime
    mock: bool = False


class YouTubeChannelSignals(BaseModel):
    channel_id: str
    entity: str
    name: str
    observations: list[NormalizedObservation]
    fetched_at: datetime
    mock: bool = False


class GoogleTrendsSignals(BaseModel):
    query: str
    entity: str
    region: str
    provider: str
    observations: list[NormalizedObservation]
    fetched_at: datetime
    mock: bool = False


class YouTubeSearchCandidate(BaseModel):
    """One channel candidate from a bounded identity-discovery search (evidence, not a decision)."""

    channel_id: str
    title: str
    description: str = ""
    handle: str | None = None
    canonical_url: str
    topic_signal: bool = False


class YouTubeSearchResult(BaseModel):
    query: str
    candidates: list[YouTubeSearchCandidate]
    fetched_at: datetime
    mock: bool = False


class YouTubeChannelVerification(BaseModel):
    """Authoritative channels.list existence check for a KNOWN channel id (Phase 5A.1a).

    ``status`` is FOUND or CHANNEL_NOT_FOUND (an empty ``items`` array is NOT_FOUND, never a failure).
    A provider/network failure is surfaced as an HTTP error by the route, never as CHANNEL_NOT_FOUND."""

    status: str                                  # FOUND | CHANNEL_NOT_FOUND
    channel_id: str
    title: str | None = None
    handle: str | None = None                    # snippet.customUrl where available
    subscriber_count: int | None = None
    total_view_count: int | None = None
    video_count: int | None = None
    fetched_at: datetime
    mock: bool = False


class YouTubeVideoStat(BaseModel):
    video_id: str
    title: str | None = None
    published_at: datetime | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None


class YouTubeVideoSignals(BaseModel):
    channel_id: str
    uploads_playlist_id: str | None = None
    videos: list[YouTubeVideoStat]
    fetched_at: datetime
    mock: bool = False


class YouTubeVideoBatchResult(BaseModel):
    """videos.list batch statistics for known video ids (Phase 5A.3). One quota unit for up to 50 ids —
    far cheaper than per-video reads for high-volume known-video refreshes."""
    requested: int
    videos: list[YouTubeVideoStat]
    fetched_at: datetime
    mock: bool = False
