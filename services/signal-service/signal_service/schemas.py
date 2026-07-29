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
