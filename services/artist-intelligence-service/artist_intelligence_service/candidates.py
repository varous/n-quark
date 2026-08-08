"""Artist candidate ledger + India market-presence evidence (Phase 5A.3).

The candidate layer decouples the artist universe from ticketing coverage: artists can be *discovered*
from many surfaces (event-derived, YouTube search/ecosystem, future ingestion seams) and only become
linked to a canonical ARTIST through the existing entity architecture. Two hard rules:

- a candidate is NEVER a canonical artist and NEVER creates one (canonical identity stays owned by the
  entity/graph architecture — this layer only proposes and links);
- candidate creation is idempotent on (discovery_source, discovery_source_id): repeated discovery of the
  same source id merges evidence + bumps last_seen, it does not explode duplicate rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artist_intelligence_service import identity as idlib
from artist_intelligence_service.models import ArtistCandidate, ArtistMarketEvidence

# candidate statuses
NEW = "NEW"
RESOLUTION_PENDING = "RESOLUTION_PENDING"
RESOLVED = "RESOLVED"
AMBIGUOUS = "AMBIGUOUS"
REJECTED = "REJECTED"

# discovery sources
SRC_EVENT = "EVENT"
SRC_YOUTUBE_SEARCH = "YOUTUBE_SEARCH"
SRC_YOUTUBE_ECOSYSTEM = "YOUTUBE_ECOSYSTEM"
SRC_IMPORT = "IMPORT"


def _now() -> datetime:
    return datetime.now(UTC)


def upsert_candidate(db: Session, *, display_name: str, discovery_source: str,
                     discovery_source_id: str, discovery_method: str | None = None,
                     source_url: str | None = None, hints: dict[str, Any] | None = None,
                     evidence: dict[str, Any] | None = None, provenance: dict[str, Any] | None = None,
                     canonical_artist_id: str | None = None, status: str | None = None,
                     now: datetime | None = None) -> tuple[ArtistCandidate, bool]:
    """Idempotent create-or-merge on (discovery_source, discovery_source_id). Returns (candidate, created)."""
    now = now or _now()
    hints = hints or {}
    existing = db.execute(
        select(ArtistCandidate).where(
            ArtistCandidate.discovery_source == discovery_source,
            ArtistCandidate.discovery_source_id == discovery_source_id)
    ).scalar_one_or_none()
    if existing is not None:
        # merge evidence (append reference count + refresh recency); never downgrade a resolved link.
        existing.last_seen_at = now
        existing.evidence_refs += 1
        if evidence:
            existing.evidence = {**(existing.evidence or {}), **evidence}
        if display_name and not existing.display_name:
            existing.display_name = display_name
        if canonical_artist_id and not existing.canonical_artist_id:
            existing.canonical_artist_id = canonical_artist_id
            existing.status = status or RESOLVED
        existing.updated_at = now
        db.flush()
        return existing, False

    row = ArtistCandidate(
        id=idlib.new_id(discovery_source, discovery_source_id),
        display_name=display_name or discovery_source_id, discovery_source=discovery_source,
        discovery_source_id=discovery_source_id, discovery_method=discovery_method, source_url=source_url,
        country_hint=hints.get("country"), region_hint=hints.get("region"),
        language_hint=hints.get("language"), genre_hint=hints.get("genre"),
        status=status or (RESOLVED if canonical_artist_id else NEW),
        canonical_artist_id=canonical_artist_id, evidence_refs=1,
        first_seen_at=now, last_seen_at=now, evidence=evidence or {}, provenance=provenance or {},
        created_at=now, updated_at=now)
    db.add(row)
    db.flush()
    return row, True


def link_to_canonical(db: Session, candidate: ArtistCandidate, canonical_artist_id: str, *,
                      status: str = RESOLVED, now: datetime | None = None) -> ArtistCandidate:
    """Attach a candidate to an EXISTING canonical artist (never creates one). Preserves evidence."""
    candidate.canonical_artist_id = canonical_artist_id
    candidate.status = status
    candidate.updated_at = now or _now()
    db.flush()
    return candidate


def set_status(db: Session, candidate: ArtistCandidate, status: str, *,
               now: datetime | None = None) -> ArtistCandidate:
    candidate.status = status
    candidate.updated_at = now or _now()
    db.flush()
    return candidate


def record_market_evidence(db: Session, *, canonical_artist_id: str, evidence_class: str,
                           source: str, source_ref: str, country: str = "IN",
                           observed_at: datetime | None = None,
                           provenance: dict[str, Any] | None = None,
                           now: datetime | None = None) -> tuple[ArtistMarketEvidence, bool]:
    """Append India market-presence evidence, idempotent on
    (canonical_artist_id, evidence_class, source, source_ref). Provenance + dates preserved."""
    now = now or _now()
    key = idlib.new_id(canonical_artist_id, evidence_class, source, source_ref)
    existing = db.execute(
        select(ArtistMarketEvidence).where(ArtistMarketEvidence.id == key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False
    row = ArtistMarketEvidence(
        id=key, canonical_artist_id=canonical_artist_id, country=country,
        evidence_class=evidence_class, source=source, source_ref=source_ref,
        observed_at=observed_at or now, provenance=provenance or {}, created_at=now)
    db.add(row)
    db.flush()
    return row, True


def evidence_classes_for(db: Session, canonical_artist_id: str) -> set[str]:
    rows = db.execute(
        select(ArtistMarketEvidence.evidence_class).where(
            ArtistMarketEvidence.canonical_artist_id == canonical_artist_id)).scalars()
    return set(rows)


def candidate_counts(db: Session) -> dict[str, int]:
    rows = db.execute(select(ArtistCandidate.status, func.count()).group_by(ArtistCandidate.status)).all()
    by_status = {s: c for s, c in rows}
    return {
        "total": sum(by_status.values()),
        "new": by_status.get(NEW, 0), "resolution_pending": by_status.get(RESOLUTION_PENDING, 0),
        "resolved": by_status.get(RESOLVED, 0), "ambiguous": by_status.get(AMBIGUOUS, 0),
        "rejected": by_status.get(REJECTED, 0)}
