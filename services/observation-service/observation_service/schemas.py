from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class ObservationCreate(BaseModel):
    """Payload for append-only observation ingestion."""

    entity: str = Field(min_length=1, max_length=512)
    attribute: str = Field(min_length=1, max_length=255)
    value: Any
    source: str = Field(min_length=1, max_length=255)
    timestamp: datetime | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def ensure_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class ObservationRead(BaseModel):
    id: UUID
    entity: str
    attribute: str
    value: Any
    source: str
    timestamp: datetime
    confidence: float
    evidence: dict[str, Any]
    metadata: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class ObservationListResponse(BaseModel):
    entity: str
    count: int
    observations: list[ObservationRead]


class ObservationCreatedResponse(BaseModel):
    observation: ObservationRead


class RecentObservationsResponse(BaseModel):
    count: int
    observations: list[ObservationRead]


def new_observation_id() -> UUID:
    return uuid4()
