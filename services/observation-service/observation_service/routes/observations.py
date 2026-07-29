from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from uuid import UUID

from observation_service.db import get_db
from observation_service.repository import (
    count_observations,
    count_observations_for_entity,
    create_observation,
    get_observation,
    list_observations_for_entity,
    list_recent_observations,
)
from observation_service.schemas import (
    ObservationCreate,
    ObservationCreatedResponse,
    ObservationListResponse,
    ObservationRead,
    RecentObservationsResponse,
)

router = APIRouter(prefix="/v1/observations", tags=["observations"])


@router.post(
    "",
    response_model=ObservationCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Append an immutable observation",
)
def append_observation(
    payload: ObservationCreate,
    db: Session = Depends(get_db),
) -> ObservationCreatedResponse:
    observation = create_observation(db, payload)
    return ObservationCreatedResponse(observation=observation)


@router.get(
    "/recent",
    response_model=RecentObservationsResponse,
    summary="List recent observations across all entities",
)
def list_recent(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> RecentObservationsResponse:
    observations = list_recent_observations(db, limit=limit, offset=offset)
    total = count_observations(db)
    return RecentObservationsResponse(count=total, observations=observations)


@router.get(
    "/by-id/{observation_id}",
    response_model=ObservationRead,
    summary="Fetch a single observation by id",
)
def read_observation(
    observation_id: UUID,
    db: Session = Depends(get_db),
) -> ObservationRead:
    observation = get_observation(db, observation_id)
    if observation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observation not found")
    return observation


@router.get(
    "/{entity_id}",
    response_model=ObservationListResponse,
    summary="List observations for an entity",
)
def list_entity_observations(
    entity_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ObservationListResponse:
    observations = list_observations_for_entity(db, entity_id, limit=limit, offset=offset)
    total = count_observations_for_entity(db, entity_id)
    return ObservationListResponse(entity=entity_id, count=total, observations=observations)
