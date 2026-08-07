"""DB read helpers for the demand ledger (Phase 5A). Thin queries; computation lives in readmodels.py."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artist_intelligence_service.models import ArtistDemandObservation as ADO


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def series(db: Session, artist: str, provider: str, metric: str, *, scope_type: str = "GLOBAL",
           scope_id: str | None = None) -> list[tuple[datetime, float]]:
    """Ascending (observed_at, value) series for one metric+scope. GLOBAL = channel-level."""
    q = (select(ADO.observed_at, ADO.value_numeric)
         .where(ADO.canonical_artist_id == artist, ADO.provider == provider, ADO.metric == metric,
                ADO.scope_type == scope_type, ADO.value_numeric.is_not(None))
         .order_by(ADO.observed_at))
    if scope_id is not None:
        q = q.where(ADO.scope_id == scope_id)
    return [(_aware(dt), float(v)) for dt, v in db.execute(q).all()]


def content_series(db: Session, artist: str, metric: str) -> dict[str, list[tuple[datetime, float]]]:
    """Per-video ascending series for a CONTENT metric (keyed by scope_id = video_id)."""
    rows = db.execute(
        select(ADO.scope_id, ADO.observed_at, ADO.value_numeric)
        .where(ADO.canonical_artist_id == artist, ADO.metric == metric, ADO.scope_type == "CONTENT",
               ADO.value_numeric.is_not(None))
        .order_by(ADO.scope_id, ADO.observed_at)
    ).all()
    out: dict[str, list[tuple[datetime, float]]] = {}
    for vid, dt, v in rows:
        out.setdefault(vid, []).append((_aware(dt), float(v)))
    return out


def latest_by_scope(db: Session, artist: str, provider: str, metric: str,
                    scope_type: str) -> list[dict[str, Any]]:
    """Latest observation per scope_id (e.g. regional interest, or per-video current stats)."""
    rows = db.execute(
        select(ADO.scope_id, ADO.scope_label, ADO.observed_at, ADO.value_numeric,
               ADO.provider_timestamp, ADO.provenance, ADO.evidence_status)
        .where(ADO.canonical_artist_id == artist, ADO.provider == provider, ADO.metric == metric,
               ADO.scope_type == scope_type)
        .order_by(ADO.scope_id, ADO.observed_at)
    ).all()
    latest: dict[str, dict[str, Any]] = {}
    for scope_id, label, dt, val, pts, prov, ev in rows:
        latest[scope_id] = {"scope_id": scope_id, "scope_label": label, "observed_at": _aware(dt),
                            "value": val, "provider_timestamp": _aware(pts), "provenance": prov or {},
                            "evidence_status": ev}
    return list(latest.values())


def video_published_dates(db: Session, artist: str) -> list[datetime]:
    """Distinct upload timestamps of tracked videos (for cadence / recent-upload counts)."""
    rows = db.execute(
        select(ADO.scope_id, func.max(ADO.provider_timestamp))
        .where(ADO.canonical_artist_id == artist, ADO.scope_type == "CONTENT",
               ADO.provider_timestamp.is_not(None))
        .group_by(ADO.scope_id)
    ).all()
    return [aware for _vid, dt in rows if (aware := _aware(dt)) is not None]


def observation_count(db: Session, artist: str) -> int:
    return int(db.execute(
        select(func.count()).select_from(ADO).where(ADO.canonical_artist_id == artist)
    ).scalar_one())


def list_observations(db: Session, artist: str, *, provider: str | None = None,
                      metric: str | None = None, scope_id: str | None = None,
                      date_from: datetime | None = None, date_to: datetime | None = None,
                      limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """Bounded, filterable observation history (date range / provider / metric / geography)."""
    conds = [ADO.canonical_artist_id == artist]
    if provider:
        conds.append(ADO.provider == provider)
    if metric:
        conds.append(ADO.metric == metric)
    if scope_id:
        conds.append(ADO.scope_id == scope_id)
    if date_from:
        conds.append(ADO.observed_at >= date_from)
    if date_to:
        conds.append(ADO.observed_at <= date_to)
    total = int(db.execute(select(func.count()).select_from(ADO).where(*conds)).scalar_one())
    rows = db.execute(
        select(ADO).where(*conds).order_by(ADO.observed_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    items = [{"id": r.id, "provider": r.provider, "metric": r.metric,
              "value_numeric": r.value_numeric, "value_text": r.value_text, "unit": r.unit,
              "scope_type": r.scope_type, "scope_id": r.scope_id, "scope_label": r.scope_label,
              "provider_timestamp": _aware(r.provider_timestamp).isoformat() if r.provider_timestamp else None,
              "observed_at": _aware(r.observed_at).isoformat(), "evidence_status": r.evidence_status}
             for r in rows]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def first_last_observed(db: Session, artist: str) -> tuple[datetime | None, datetime | None]:
    row = db.execute(
        select(func.min(ADO.observed_at), func.max(ADO.observed_at))
        .where(ADO.canonical_artist_id == artist)
    ).one()
    return _aware(row[0]), _aware(row[1])
