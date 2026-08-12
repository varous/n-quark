"""Candidate → canonical artist promotion (Phase 5A.3.1).

Closes the gap where independently-discovered candidates were stranded forever. Promotion is deterministic
and auditable, and canonical ownership stays OUTSIDE this service:

- MATCH_EXISTING_CANONICAL — the candidate's name matches an existing canonical ARTIST → LINK (no create).
- MULTI_SOURCE_CONFIRMED — the same artist was seen from ≥N independent discovery sources → create via the
  crawl entity owner.
- INDIA_LIVE_EVIDENCE_PLUS_MUSIC_IDENTITY — an EVENT-sourced sibling (India live) plus a music identity
  signal → create via the crawl entity owner.

A single YouTube search hit satisfies none of these, so weak evidence never canonicalises. Creation always
goes through crawl (`create_artist`); the demand service never writes a canonical id to its own DB. After a
candidate links/creates, demand identity resolution is enqueued automatically (idempotent).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artist_intelligence_service import candidates as cand
from artist_intelligence_service import cadence as cad
from artist_intelligence_service.config import settings
from artist_intelligence_service.crawl_client import CrawlServiceClient
from artist_intelligence_service.models import ArtistCandidate, ArtistExternalIdentity
from artist_intelligence_service.providers.base import (
    INDIA_MARKET_CANDIDATE,
    PROVIDER_YOUTUBE,
    RESOLVED,
)
from artist_intelligence_service.scheduler import DemandScheduler

MATCH_EXISTING_CANONICAL = "MATCH_EXISTING_CANONICAL"
MULTI_SOURCE_CONFIRMED = "MULTI_SOURCE_CONFIRMED"
INDIA_LIVE_PLUS_MUSIC = "INDIA_LIVE_EVIDENCE_PLUS_MUSIC_IDENTITY"


def _now() -> datetime:
    return datetime.now(UTC)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _has_resolved_youtube(db: Session, canonical_artist_id: str) -> bool:
    return db.execute(
        select(ArtistExternalIdentity.id).where(
            ArtistExternalIdentity.canonical_artist_id == canonical_artist_id,
            ArtistExternalIdentity.provider == PROVIDER_YOUTUBE,
            ArtistExternalIdentity.identity_type == "CHANNEL_ID",
            ArtistExternalIdentity.status == RESOLVED).limit(1)
    ).scalar_one_or_none() is not None


def _distinct_sources_for_name(db: Session, display_name: str) -> set[str]:
    """Distinct discovery sources across all candidates sharing this normalized name (independence)."""
    target = _norm(display_name)
    rows = db.execute(select(ArtistCandidate.display_name, ArtistCandidate.discovery_source)).all()
    return {src for name, src in rows if _norm(name) == target}


def _has_music_identity_signal(candidate: ArtistCandidate) -> bool:
    ev = candidate.evidence or {}
    return bool(ev.get("topic_signal")) or bool(ev.get("music_identity"))


async def evaluate(db: Session, candidate: ArtistCandidate, *,
                   crawl: CrawlServiceClient) -> dict[str, Any]:
    """Deterministic promotion decision. Returns {promote, method, reason, canonical_artist_id?}."""
    if candidate.canonical_artist_id:
        return {"promote": True, "method": "ALREADY_LINKED", "reason": "candidate already linked",
                "canonical_artist_id": candidate.canonical_artist_id}

    # 1) MATCH_EXISTING_CANONICAL — link to an existing canonical artist (never create).
    match = await crawl.find_artist_by_name(candidate.display_name)
    if match:
        return {"promote": True, "method": MATCH_EXISTING_CANONICAL,
                "reason": "matched existing canonical artist",
                "canonical_artist_id": match["canonical_entity_id"]}

    # 2) MULTI_SOURCE_CONFIRMED — ≥N independent discovery sources for this artist.
    sources = _distinct_sources_for_name(db, candidate.display_name)
    if len(sources) >= settings.candidate_promotion_min_sources:
        return {"promote": True, "method": MULTI_SOURCE_CONFIRMED,
                "reason": f"{len(sources)} independent sources: {sorted(sources)}",
                "canonical_artist_id": None}

    # 3) INDIA_LIVE_EVIDENCE_PLUS_MUSIC_IDENTITY — an EVENT-sourced sibling + a music identity signal.
    if cand.SRC_EVENT in sources and _has_music_identity_signal(candidate):
        return {"promote": True, "method": INDIA_LIVE_PLUS_MUSIC,
                "reason": "India live (event) evidence + music identity signal",
                "canonical_artist_id": None}

    return {"promote": False, "method": None,
            "reason": "insufficient evidence (single weak source)", "canonical_artist_id": None}


async def promote(db: Session, candidate: ArtistCandidate, *, crawl: CrawlServiceClient | None = None,
                  scheduler: DemandScheduler | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Evaluate + (if eligible) link/create through the crawl owner, then auto-onboard demand. Idempotent."""
    now = now or _now()
    crawl = crawl or CrawlServiceClient()
    scheduler = scheduler or DemandScheduler()

    decision = await evaluate(db, candidate, crawl=crawl)
    if not decision["promote"]:
        # keep it visible as attempted-but-insufficient; stays non-canonical.
        if candidate.status == cand.NEW:
            cand.set_status(db, candidate, cand.RESOLUTION_PENDING, now=now)
        return {"promoted": False, "candidate_id": candidate.id, **decision}

    cid = decision.get("canonical_artist_id")
    created = False
    if cid is None:
        # creation goes through the crawl entity owner — never written here.
        result = await crawl.create_artist(
            candidate.display_name, source=candidate.discovery_source,
            provenance={"candidate_id": candidate.id, "method": decision["method"],
                        "discovery_source": candidate.discovery_source})
        cid = result["canonical_entity_id"]
        created = bool(result.get("created"))

    # 5B.1.1 — canonical-reference invariant: re-read/confirm the id against the AUTHORITATIVE crawl
    # registry before any downstream linking. This never trusts a stale candidate.canonical_artist_id
    # (ALREADY_LINKED) or a create that failed to register: an id the owner does not acknowledge is not
    # canonical. We never fabricate a registry row here — the id must already be owner-registered (create
    # does that idempotently). A crawl outage raises and is handled by the caller (fail closed).
    if not await crawl.canonical_artist_registered(cid):
        if candidate.status == cand.NEW:
            cand.set_status(db, candidate, cand.RESOLUTION_PENDING, now=now)
        db.flush()
        return {"promoted": False, "candidate_id": candidate.id, "method": "CANONICAL_UNVERIFIED",
                "reason": f"canonical id {cid} is not acknowledged by the crawl registry",
                "canonical_artist_id": None, "proposed_canonical_artist_id": cid,
                "created_canonical": created}

    cand.link_to_canonical(db, candidate, cid, now=now)
    cand.record_market_evidence(
        db, canonical_artist_id=cid, evidence_class=INDIA_MARKET_CANDIDATE,
        source=candidate.discovery_source, source_ref=candidate.discovery_source_id,
        provenance={"promotion_method": decision["method"], "promoted_at": now.isoformat()}, now=now)

    queued = False
    if not _has_resolved_youtube(db, cid):
        queued = scheduler.enqueue_identity_discovery(
            db, cid, display_name=candidate.display_name, priority=cad.P3_YT_DISCOVERY, now=now)
    db.flush()
    return {"promoted": True, "candidate_id": candidate.id, "canonical_artist_id": cid,
            "created_canonical": created, "identity_discovery_queued": queued, **decision}


async def process_backlog(db: Session, *, crawl: CrawlServiceClient | None = None,
                          scheduler: DemandScheduler | None = None, limit: int | None = None,
                          now: datetime | None = None) -> dict[str, Any]:
    """Bounded pass over NEW / RESOLUTION_PENDING / AMBIGUOUS candidates (persisted work, never the whole
    backlog synchronously)."""
    now = now or _now()
    limit = limit or settings.candidate_promotion_batch_size
    crawl = crawl or CrawlServiceClient()
    scheduler = scheduler or DemandScheduler()
    rows = db.execute(
        select(ArtistCandidate).where(
            ArtistCandidate.status.in_((cand.NEW, cand.RESOLUTION_PENDING, cand.AMBIGUOUS)))
        .order_by(ArtistCandidate.evidence_refs.desc(), ArtistCandidate.first_seen_at)
        .limit(limit)).scalars().all()
    promoted = evaluated = 0
    for c in rows:
        evaluated += 1
        out = await promote(db, c, crawl=crawl, scheduler=scheduler, now=now)
        promoted += 1 if out["promoted"] else 0
    db.commit()
    return {"evaluated": evaluated, "promoted": promoted, "batch_limit": limit}
