"""Shared schemas and utilities for n-quark services."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ObservationProvenance(BaseModel):
    """Compliance envelope carried by every observation (stored in metadata["provenance"]).

    India-first profile: the platform stays entity-level (artist / venue / event /
    organizer / community) and never ingests individual-fan-level personal data.
    Enforcement of these rules lives in each ingest service; this is the canonical shape.
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


class Observation(BaseModel):
    """Immutable observation record."""

    id: UUID
    entity: str
    attribute: str
    value: Any
    source: str
    timestamp: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Feature(BaseModel):
    """Versioned ML-ready feature."""

    id: UUID
    entity: str
    name: str
    value: Any
    version: str
    calculated_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)


class Prediction(BaseModel):
    """Intelligence engine output."""

    id: UUID
    engine: str
    entity: str
    output: dict[str, Any]
    confidence: float = Field(ge=0.0, le=1.0)
    model_version: str
    feature_version: str
    generated_at: datetime
