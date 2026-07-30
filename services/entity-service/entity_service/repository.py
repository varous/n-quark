from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from entity_service.config import canonical_id, slugify
from entity_service.models import EntityAliasRecord, EntityRecord
from entity_service.schemas import (
    ArtistCreate,
    EntityAliasRead,
    EntityRead,
    EntityResolveRequest,
    EntityResolveResponse,
)


class EntityNotFoundError(Exception):
    pass


class EntityResolutionError(Exception):
    pass


def _to_entity_read(record: EntityRecord) -> EntityRead:
    return EntityRead(
        id=record.id,
        entity_type=record.entity_type,
        display_name=record.display_name,
        metadata=record.entity_metadata,
        created_at=record.created_at,
        updated_at=record.updated_at,
        aliases=[
            EntityAliasRead(
                alias_key=alias.alias_key,
                source=alias.source,
                created_at=alias.created_at,
            )
            for alias in record.aliases
        ],
    )


def _unique_entity_id(
    db: Session,
    entity_type: str,
    display_name: str,
    slug: str | None = None,
) -> str:
    if slug:
        candidate = f"{slugify(entity_type)}:{slugify(slug)}"
    else:
        candidate = canonical_id(entity_type, display_name)

    existing = db.get(EntityRecord, candidate)
    if existing is None:
        return candidate

    for _ in range(10):
        candidate = canonical_id(entity_type, display_name, suffix=uuid4().hex[:8])
        if db.get(EntityRecord, candidate) is None:
            return candidate

    raise EntityResolutionError("Unable to allocate unique canonical entity id")


def create_entity(
    db: Session,
    *,
    entity_type: str,
    display_name: str,
    metadata: dict | None = None,
    aliases: list[str] | None = None,
    alias_source: str = "manual",
    slug: str | None = None,
) -> tuple[EntityRead, bool]:
    """Create a canonical entity of any type. Returns (entity, created)."""
    now = datetime.now(UTC)
    new_id = _unique_entity_id(db, entity_type, display_name, slug)

    existing = db.get(EntityRecord, new_id)
    if existing is not None:
        _link_aliases(db, existing.id, aliases or [], alias_source, now)
        db.commit()
        db.refresh(existing)
        return _to_entity_read(existing), False

    record = EntityRecord(
        id=new_id,
        entity_type=entity_type,
        display_name=display_name,
        entity_metadata=metadata or {},
        created_at=now,
        updated_at=now,
    )
    db.add(record)
    db.flush()
    _link_aliases(db, record.id, aliases or [], alias_source, now)
    db.commit()
    db.refresh(record)
    return _to_entity_read(record), True


def create_artist(db: Session, payload: ArtistCreate) -> tuple[EntityRead, bool]:
    """Create canonical artist. Returns (entity, created)."""
    return create_entity(
        db,
        entity_type="artist",
        display_name=payload.display_name,
        metadata=payload.metadata,
        aliases=payload.aliases,
        alias_source=payload.alias_source,
        slug=payload.slug,
    )


def add_aliases(
    db: Session,
    canonical_id: str,
    aliases: list[str],
    source: str = "identity-crossref",
) -> EntityRead:
    """Fold extra aliases (e.g. external identity ids) onto an existing canonical entity.

    Idempotent: re-linking the same alias is a no-op. Raises if the alias is already claimed
    by a *different* canonical entity (that would silently merge two identities).
    """
    record = db.get(EntityRecord, canonical_id)
    if record is None:
        raise EntityNotFoundError(canonical_id)
    _link_aliases(db, canonical_id, aliases, source, datetime.now(UTC))
    db.commit()
    return get_entity(db, canonical_id)


def get_entity(db: Session, canonical_id: str) -> EntityRead:
    stmt = (
        select(EntityRecord)
        .where(EntityRecord.id == canonical_id)
        .options(selectinload(EntityRecord.aliases))
    )
    record = db.scalars(stmt).first()
    if record is None:
        raise EntityNotFoundError(canonical_id)
    return _to_entity_read(record)


def get_entity_by_alias(db: Session, alias_key: str) -> EntityRead:
    stmt = (
        select(EntityAliasRecord)
        .where(EntityAliasRecord.alias_key == alias_key)
        .options(selectinload(EntityAliasRecord.entity).selectinload(EntityRecord.aliases))
    )
    alias = db.scalars(stmt).first()
    if alias is None:
        raise EntityNotFoundError(alias_key)
    return _to_entity_read(alias.entity)


def resolve_entity(db: Session, payload: EntityResolveRequest) -> EntityResolveResponse:
    """Resolve an external alias to a canonical entity, optionally creating one."""
    alias_stmt = (
        select(EntityAliasRecord)
        .where(EntityAliasRecord.alias_key == payload.alias)
        .options(selectinload(EntityAliasRecord.entity).selectinload(EntityRecord.aliases))
    )
    existing_alias = db.scalars(alias_stmt).first()
    if existing_alias is not None:
        return EntityResolveResponse(
            canonical_id=existing_alias.canonical_id,
            entity=_to_entity_read(existing_alias.entity),
            created=False,
            alias_linked=False,
        )

    if not payload.create_if_missing:
        raise EntityNotFoundError(payload.alias)

    if not payload.display_name:
        raise EntityResolutionError("display_name is required to create a new canonical entity")

    entity_read, created = create_entity(
        db,
        entity_type=payload.entity_type,
        display_name=payload.display_name,
        metadata=payload.metadata,
        aliases=[payload.alias],
        alias_source=payload.source,
    )
    return EntityResolveResponse(
        canonical_id=entity_read.id,
        entity=entity_read,
        created=created,
        alias_linked=True,
    )


def resolve_spotify_artist(
    db: Session,
    spotify_id: str,
    display_name: str,
) -> EntityResolveResponse:
    alias = f"artist:spotify:{spotify_id}"
    return resolve_entity(
        db,
        EntityResolveRequest(
            alias=alias,
            entity_type="artist",
            display_name=display_name,
            source="spotify",
            metadata={"spotify_id": spotify_id},
            create_if_missing=True,
        ),
    )


def _link_aliases(
    db: Session,
    canonical_id: str,
    aliases: list[str],
    source: str,
    now: datetime,
) -> None:
    for alias_key in aliases:
        if not alias_key:
            continue
        existing = db.get(EntityAliasRecord, alias_key)
        if existing is not None:
            if existing.canonical_id != canonical_id:
                raise EntityResolutionError(
                    f"Alias {alias_key} already mapped to {existing.canonical_id}"
                )
            continue
        db.add(
            EntityAliasRecord(
                alias_key=alias_key,
                canonical_id=canonical_id,
                source=source,
                created_at=now,
            )
        )
