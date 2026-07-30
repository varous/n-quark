from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ArtistCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=512)
    slug: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    alias_source: str = "manual"


class EntityResolveRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=512)
    entity_type: str = Field(default="artist", max_length=64)
    display_name: str | None = Field(default=None, max_length=512)
    source: str = Field(default="unknown", max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)
    create_if_missing: bool = True


class AliasLinkRequest(BaseModel):
    """Fold external identity cross-references (MBID, Google KG mID) in as aliases.

    Keys should be namespaced by scheme (e.g. ``mbid:...``, ``kgmid:/m/...``) so an opaque
    external id never collides with a source handle or another scheme's id.
    """

    aliases: list[str] = Field(min_length=1)
    source: str = Field(default="identity-crossref", max_length=128)


class EntityAliasRead(BaseModel):
    alias_key: str
    source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class EntityRead(BaseModel):
    id: str
    entity_type: str
    display_name: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    aliases: list[EntityAliasRead] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class EntityResolveResponse(BaseModel):
    canonical_id: str
    entity: EntityRead
    created: bool
    alias_linked: bool
