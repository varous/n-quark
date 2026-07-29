from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from entity_service.db import get_db
from entity_service.repository import (
    EntityNotFoundError,
    EntityResolutionError,
    create_artist,
    get_entity,
    get_entity_by_alias,
    resolve_entity,
    resolve_spotify_artist,
)
from entity_service.schemas import (
    ArtistCreate,
    EntityRead,
    EntityResolveRequest,
    EntityResolveResponse,
)

router = APIRouter(prefix="/v1/entities", tags=["entities"])


class SpotifyArtistResolveRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=512)


@router.post(
    "/artists",
    response_model=EntityRead,
    summary="Register a canonical artist",
)
def register_artist(
    payload: ArtistCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> EntityRead:
    try:
        entity, created = create_artist(db, payload)
    except EntityResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return entity


@router.post(
    "/resolve",
    response_model=EntityResolveResponse,
    summary="Resolve external alias to canonical entity",
)
def resolve_alias(payload: EntityResolveRequest, db: Session = Depends(get_db)) -> EntityResolveResponse:
    try:
        return resolve_entity(db, payload)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except EntityResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post(
    "/artists/resolve-spotify/{spotify_id}",
    response_model=EntityResolveResponse,
    summary="Resolve Spotify artist id to canonical artist",
)
def resolve_spotify(
    spotify_id: str,
    payload: SpotifyArtistResolveRequest,
    db: Session = Depends(get_db),
) -> EntityResolveResponse:
    try:
        return resolve_spotify_artist(db, spotify_id, payload.display_name)
    except EntityResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get(
    "/by-alias/{alias_key:path}",
    response_model=EntityRead,
    summary="Lookup canonical entity by alias key",
)
def read_by_alias(alias_key: str, db: Session = Depends(get_db)) -> EntityRead:
    try:
        return get_entity_by_alias(db, alias_key)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/{canonical_id:path}",
    response_model=EntityRead,
    summary="Get canonical entity by id",
)
def read_entity(canonical_id: str, db: Session = Depends(get_db)) -> EntityRead:
    try:
        return get_entity(db, canonical_id)
    except EntityNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
