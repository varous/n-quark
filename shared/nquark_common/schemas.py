"""Shared schemas and utilities for n-quark services."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


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
