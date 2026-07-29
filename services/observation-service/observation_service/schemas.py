from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class ObservationProvenance(BaseModel):
    """Compliance envelope validated when present in metadata["provenance"].

    Enforces the India-first ingest rules: entity-level only, no PII, logged-out +
    robots-respecting for scraped sources, consent for partner feeds.
    """

    acquisition_method: Literal[
        "official_api", "aggregator_api", "public_scrape", "partner_feed"
    ]
    legal_basis: Literal[
        "platform_api_tos",
        "aggregator_contract",
        "public_figure_professional",
        "legitimate_interest",
    ]
    data_subject_type: Literal["entity", "individual"] = "entity"
    contains_pii: bool = False
    adapter_version: str
    collected_at: datetime
    source_url: str | None = None
    robots_respected: bool | None = None
    logged_out: bool | None = None
    consent_source: str | None = None

    @model_validator(mode="after")
    def enforce_compliance(self) -> "ObservationProvenance":
        if self.data_subject_type == "individual" or self.contains_pii:
            raise ValueError(
                "India-first profile: individual / PII observations are not accepted"
            )
        if self.acquisition_method == "public_scrape" and not (
            self.logged_out and self.robots_respected
        ):
            raise ValueError(
                "public_scrape requires logged_out=true and robots_respected=true"
            )
        if self.acquisition_method == "partner_feed" and not self.consent_source:
            raise ValueError("partner_feed requires consent_source")
        return self


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

    @model_validator(mode="after")
    def validate_provenance(self) -> "ObservationCreate":
        """When a provenance block is supplied, it must satisfy the compliance rules."""
        provenance = self.metadata.get("provenance") if self.metadata else None
        if provenance is not None:
            ObservationProvenance.model_validate(provenance)
        return self


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


class ObservationBulkCreate(BaseModel):
    """Append many observations in one request (avoids N+1 writes from crawlers)."""

    observations: list[ObservationCreate] = Field(min_length=1, max_length=500)


class ObservationBulkCreatedResponse(BaseModel):
    count: int
    observations: list[ObservationRead]


class RecentObservationsResponse(BaseModel):
    count: int
    observations: list[ObservationRead]


def new_observation_id() -> UUID:
    return uuid4()
