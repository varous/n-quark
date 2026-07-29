from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from observation_service.models import ObservationRecord
from observation_service.schemas import ObservationCreate, ObservationRead, new_observation_id


def _build_record(payload: ObservationCreate, now: datetime) -> ObservationRecord:
    return ObservationRecord(
        id=new_observation_id(),
        entity=payload.entity,
        attribute=payload.attribute,
        value=payload.value,
        source=payload.source,
        timestamp=payload.timestamp or now,
        confidence=payload.confidence,
        evidence=payload.evidence,
        observation_metadata=payload.metadata,
        created_at=now,
    )


def create_observation(db: Session, payload: ObservationCreate) -> ObservationRead:
    """Persist a new observation. Append-only — never updates existing rows."""
    now = datetime.now(UTC)
    record = _build_record(payload, now)
    db.add(record)
    db.commit()
    db.refresh(record)
    return _to_read(record)


def create_observations(db: Session, payloads: list[ObservationCreate]) -> list[ObservationRead]:
    """Append many observations in a single transaction. Append-only."""
    now = datetime.now(UTC)
    records = [_build_record(payload, now) for payload in payloads]
    db.add_all(records)
    db.commit()
    for record in records:
        db.refresh(record)
    return [_to_read(record) for record in records]


def list_recent_observations(
    db: Session,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[ObservationRead]:
    stmt = (
        select(ObservationRecord)
        .order_by(ObservationRecord.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = db.scalars(stmt).all()
    return [_to_read(row) for row in rows]


def count_observations(db: Session) -> int:
    stmt = select(func.count()).select_from(ObservationRecord)
    return db.scalar(stmt) or 0


def list_observations_for_entity(
    db: Session,
    entity_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[ObservationRead]:
    stmt = (
        select(ObservationRecord)
        .where(ObservationRecord.entity == entity_id)
        .order_by(ObservationRecord.timestamp.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = db.scalars(stmt).all()
    return [_to_read(row) for row in rows]


def count_observations_for_entity(db: Session, entity_id: str) -> int:
    stmt = (
        select(func.count())
        .select_from(ObservationRecord)
        .where(ObservationRecord.entity == entity_id)
    )
    return db.scalar(stmt) or 0


def get_observation(db: Session, observation_id: UUID) -> ObservationRead | None:
    record = db.get(ObservationRecord, observation_id)
    if record is None:
        return None
    return _to_read(record)


def _to_read(record: ObservationRecord) -> ObservationRead:
    return ObservationRead(
        id=record.id,
        entity=record.entity,
        attribute=record.attribute,
        value=record.value,
        source=record.source,
        timestamp=record.timestamp,
        confidence=record.confidence,
        evidence=record.evidence,
        metadata=record.observation_metadata,
        created_at=record.created_at,
    )
