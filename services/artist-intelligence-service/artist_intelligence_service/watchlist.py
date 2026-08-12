"""Artist intake & research watchlists (Phase 5B.1).

The one narrow operator-writable surface. An operator says "start watching this artist" and the system
records a durable *research instruction* — an ``artist_watch_target`` — then tries to resolve it and, once
resolved, onboards it into the EXISTING demand pipeline. Two invariants, inherited from the candidate
architecture and never weakened here:

- a watch target is NOT a canonical artist and NEVER creates one on its own. Canonical identity is owned
  by the entity/graph architecture; this layer only links to an existing canonical (deterministic name
  match) or promotes through the existing ``promotion`` evidence rules (≥N independent sources / event +
  music identity). An operator instruction is a single discovery source — on its own it stays pending;
- a pasted YouTube URL is a *hint*, not proof. The channel it points to is confirmed by the authoritative
  channels.list existence check before any identity becomes RESOLVED (see ``service.resolve_youtube_from_hint``).

Statuses: NEW · RESOLUTION_PENDING · WATCHING · AMBIGUOUS · REJECTED · PAUSED.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artist_intelligence_service import candidates as cand
from artist_intelligence_service import cadence as cad
from artist_intelligence_service import identity as idlib
from artist_intelligence_service import promotion, universe
from artist_intelligence_service.config import settings
from artist_intelligence_service.crawl_client import CrawlServiceClient
from artist_intelligence_service.models import ArtistExternalIdentity, ArtistWatchTarget
from artist_intelligence_service.providers.base import AMBIGUOUS as YT_AMBIGUOUS
from artist_intelligence_service.providers.base import PROVIDER_YOUTUBE
from artist_intelligence_service.providers.base import RESOLVED as YT_RESOLVED
from artist_intelligence_service.scheduler import DemandScheduler
from artist_intelligence_service.service import DemandService
from artist_intelligence_service.yturl import parse_youtube_hint

# target statuses
NEW = "NEW"
RESOLUTION_PENDING = "RESOLUTION_PENDING"
WATCHING = "WATCHING"
AMBIGUOUS = "AMBIGUOUS"
REJECTED = "REJECTED"
PAUSED = "PAUSED"

# the discovery source recorded for an operator-initiated candidate (one independent source)
SRC_OPERATOR = "OPERATOR"

_ACTIVE_STATUSES = (NEW, RESOLUTION_PENDING, WATCHING, AMBIGUOUS)


def _now() -> datetime:
    return datetime.now(UTC)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _dedup_key(*, display_name: str, canonical_artist_id: str | None,
               youtube_channel_id: str | None) -> str:
    """One target per canonical artist / provider identity / name. Canonical wins, then a verified
    channel id, then the normalized name — so the same artist is never watched twice."""
    if canonical_artist_id:
        return f"canonical:{canonical_artist_id}"
    if youtube_channel_id:
        return f"yt:{youtube_channel_id}"
    return f"name:{_norm(display_name)}"


# ---- live identity / status enrichment ---------------------------------------------------------
def youtube_identity_state(db: Session, canonical_artist_id: str | None) -> str | None:
    """The current YouTube identity state for an artist: RESOLVED | AMBIGUOUS | PENDING | None.

    RESOLVED (a verified channel) wins; else AMBIGUOUS if any ambiguous identity exists; else PENDING
    if an (unresolved) identity row exists; else None."""
    if not canonical_artist_id:
        return None
    rows = db.execute(
        select(ArtistExternalIdentity.status).where(
            ArtistExternalIdentity.canonical_artist_id == canonical_artist_id,
            ArtistExternalIdentity.provider == PROVIDER_YOUTUBE,
            ArtistExternalIdentity.identity_type == "CHANNEL_ID")).scalars().all()
    if not rows:
        return None
    if YT_RESOLVED in rows:
        return "RESOLVED"
    if YT_AMBIGUOUS in rows:
        return "AMBIGUOUS"
    return "PENDING"


def effective_status(db: Session, target: ArtistWatchTarget) -> str:
    """The target's lifecycle status derived from its canonical + live YouTube identity state. Operator
    overrides (PAUSED / REJECTED) are authoritative and never recomputed away."""
    if target.status in (PAUSED, REJECTED):
        return target.status
    if not target.canonical_artist_id:
        # NEW (never attempted) or RESOLUTION_PENDING (attempted, no canonical yet) — as last set.
        return target.status if target.status in (NEW, RESOLUTION_PENDING) else NEW
    state = youtube_identity_state(db, target.canonical_artist_id)
    if state == "AMBIGUOUS":
        return AMBIGUOUS
    # canonical present → the artist is being watched; the identity may still be resolving (that is a
    # display nuance — "finding YouTube identity" — not a separate lifecycle status).
    return WATCHING


def _human_state(status: str, identity_state: str | None) -> str:
    """Operator-facing label (never a raw enum as the primary UI language — the enum lives in Advanced)."""
    if status == WATCHING:
        return "Watching" if identity_state == "RESOLVED" else "Finding YouTube identity"
    return {
        NEW: "Queued",
        RESOLUTION_PENDING: "Waiting for stronger evidence",
        AMBIGUOUS: "Needs review",
        PAUSED: "Paused",
        REJECTED: "Removed",
    }.get(status, status)


def paused_canonical_artist_ids(db: Session) -> set[str]:
    """Canonical artist ids that are PAUSED via a watch target and have no other active watch target
    keeping them live. Consulted by the scheduler to suspend recurring collection (never deletes history)."""
    paused = set(db.execute(
        select(ArtistWatchTarget.canonical_artist_id).where(
            ArtistWatchTarget.status == PAUSED,
            ArtistWatchTarget.canonical_artist_id.is_not(None))).scalars())
    if not paused:
        return set()
    active = set(db.execute(
        select(ArtistWatchTarget.canonical_artist_id).where(
            ArtistWatchTarget.status.in_(_ACTIVE_STATUSES),
            ArtistWatchTarget.canonical_artist_id.in_(paused))).scalars())
    return paused - active


# ---- serialization -----------------------------------------------------------------------------
def serialize(db: Session, target: ArtistWatchTarget) -> dict[str, Any]:
    status = effective_status(db, target)
    identity_state = youtube_identity_state(db, target.canonical_artist_id)
    videos = 0
    last_observed = None
    if target.canonical_artist_id:
        from artist_intelligence_service.models import YouTubeVideo
        videos = int(db.execute(
            select(func.count()).select_from(YouTubeVideo).where(
                YouTubeVideo.canonical_artist_id == target.canonical_artist_id)).scalar_one())
        from artist_intelligence_service.models import ArtistDemandObservation as _ADO
        last_observed = db.execute(
            select(func.max(_ADO.observed_at)).where(
                _ADO.canonical_artist_id == target.canonical_artist_id)).scalar_one_or_none()
    return {
        "id": target.id,
        "display_name": target.display_name,
        "status": status,
        "human_state": _human_state(status, identity_state),
        "canonical_artist_id": target.canonical_artist_id,
        "youtube_identity_state": identity_state,
        "youtube_channel_id": target.youtube_channel_id,
        "youtube_hint": target.youtube_hint,
        "videos_tracked": videos,
        "last_observed_at": last_observed.isoformat() if last_observed else None,
        "source": target.source,
        "reason": target.reason,
        "priority": target.priority,
        "resolution_method": target.resolution_method,
        "created_by": target.created_by,
        "last_resolved_at": target.last_resolved_at.isoformat() if target.last_resolved_at else None,
        "created_at": target.created_at.isoformat(),
        "updated_at": target.updated_at.isoformat(),
        # technical detail for an Advanced/Evidence area (never the primary UI language)
        "detail": target.detail or {},
    }


def get_target(db: Session, target_id: str) -> ArtistWatchTarget | None:
    return db.get(ArtistWatchTarget, target_id)


def list_targets(db: Session, *, status: str | None = None, limit: int = 100,
                 offset: int = 0) -> dict[str, Any]:
    stmt = select(ArtistWatchTarget)
    if status:
        stmt = stmt.where(ArtistWatchTarget.status == status)
    total = int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())
    rows = db.execute(
        stmt.order_by(ArtistWatchTarget.created_at.desc()).limit(limit).offset(offset)).scalars().all()
    return {"total": total, "limit": limit, "offset": offset,
            "targets": [serialize(db, t) for t in rows]}


# ---- intake ------------------------------------------------------------------------------------
def _find_existing(db: Session, *, display_name: str, canonical_artist_id: str | None,
                   youtube_channel_id: str | None) -> ArtistWatchTarget | None:
    key = _dedup_key(display_name=display_name, canonical_artist_id=canonical_artist_id,
                     youtube_channel_id=youtube_channel_id)
    return db.execute(
        select(ArtistWatchTarget).where(ArtistWatchTarget.dedup_key == key)).scalar_one_or_none()


def create_target(db: Session, *, display_name: str, created_by: str,
                  canonical_artist_id: str | None = None, youtube_hint: str | None = None,
                  source: str = "OPERATOR", reason: str | None = None, priority: int | None = None,
                  now: datetime | None = None) -> tuple[ArtistWatchTarget, bool]:
    """Idempotent create-or-return on the dedup key. Does NOT resolve — resolution is a separate step so
    intake is instant and resolution can be retried. Returns (target, created)."""
    now = now or _now()
    display_name = (display_name or "").strip()
    if not display_name:
        raise ValueError("display_name required")
    priority = cad.P4_GLOBAL_CANDIDATE if priority is None else priority

    existing = _find_existing(db, display_name=display_name, canonical_artist_id=canonical_artist_id,
                              youtube_channel_id=None)
    if existing is not None:
        # merge a newly-supplied hint / canonical choice without downgrading state
        changed = False
        if youtube_hint and not existing.youtube_hint:
            existing.youtube_hint = youtube_hint
            changed = True
        if canonical_artist_id and not existing.canonical_artist_id:
            existing.canonical_artist_id = canonical_artist_id
            changed = True
        if changed:
            existing.updated_at = now
            db.flush()
        return existing, False

    key = _dedup_key(display_name=display_name, canonical_artist_id=canonical_artist_id,
                     youtube_channel_id=None)
    target = ArtistWatchTarget(
        id=idlib.new_id("watch", key, now.isoformat()), display_name=display_name, dedup_key=key,
        canonical_artist_id=canonical_artist_id, youtube_hint=youtube_hint, youtube_channel_id=None,
        source=source, reason=reason, priority=priority, status=NEW, detail={},
        created_by=created_by, created_at=now, updated_at=now)
    db.add(target)
    db.flush()
    return target, True


# ---- resolution --------------------------------------------------------------------------------
async def resolve_target(db: Session, target: ArtistWatchTarget, *,
                         crawl: CrawlServiceClient | None = None,
                         scheduler: DemandScheduler | None = None,
                         svc: DemandService | None = None,
                         now: datetime | None = None) -> dict[str, Any]:
    """Attempt to resolve + onboard a target using the EXISTING candidate/promotion/identity machinery.

    Never creates a canonical artist merely because the target exists — an operator instruction is a
    single discovery source, so a name with no existing canonical stays RESOLUTION_PENDING. A YouTube hint
    is applied (and provider-verified) only once a canonical artist is present."""
    now = now or _now()
    crawl = crawl or CrawlServiceClient()
    scheduler = scheduler or DemandScheduler()
    svc = svc or DemandService()
    if target.status in (PAUSED, REJECTED):
        return {"target_id": target.id, "status": target.status, "skipped": True}

    detail: dict[str, Any] = dict(target.detail or {})
    detail.pop("canonical_unverified", None)  # recompute freshly each resolve
    method: str | None = None
    proposed_cid: str | None = None
    promote_verified = False   # True when promotion already confirmed registry membership

    if target.canonical_artist_id:
        # Path A — operator chose an existing canonical artist. Verified against the registry below
        # BEFORE any onboarding; cleared here so an unbacked selection can never leak through.
        proposed_cid = target.canonical_artist_id
        target.canonical_artist_id = None
        method = "OPERATOR_SELECTED_CANONICAL"
    else:
        # Path B — record the operator's instruction as a candidate (one independent discovery source) and
        # run the existing deterministic promotion. Promotion itself now re-confirms registry membership,
        # so a promoted candidate's canonical id is already registry-backed.
        candidate, _ = cand.upsert_candidate(
            db, display_name=target.display_name, discovery_source=SRC_OPERATOR,
            discovery_source_id=_norm(target.display_name) or target.id,
            discovery_method="operator_watchlist", source_url=target.youtube_hint,
            evidence={"operator": True, "watch_target_id": target.id,
                      "youtube_hint": bool(target.youtube_hint)},
            provenance={"created_by": target.created_by, "source": target.source}, now=now)
        decision = await promotion.promote(db, candidate, crawl=crawl, scheduler=scheduler, now=now)
        detail["promotion"] = {k: decision.get(k) for k in ("promoted", "method", "reason")}
        target.canonical_artist_id = None
        if decision.get("promoted"):
            proposed_cid = candidate.canonical_artist_id
            promote_verified = True
            method = decision.get("method")
        else:
            method = decision.get("method") or "INSUFFICIENT_EVIDENCE"
            if decision.get("proposed_canonical_artist_id"):
                detail["canonical_unverified"] = {"id": decision["proposed_canonical_artist_id"],
                                                  "via": decision.get("method")}

    # 5B.1.1 canonical-reference invariant: only expose a canonical the crawl registry acknowledges.
    # WATCHING never references a canonical the owner does not own.
    verified_cid: str | None = None
    if proposed_cid:
        backed = promote_verified or await crawl.canonical_artist_registered(proposed_cid)
        if backed:
            verified_cid = proposed_cid
        else:
            detail["canonical_unverified"] = {
                "id": proposed_cid, "via": method,
                "reason": "not acknowledged by the crawl canonical ARTIST registry"}
            method = "CANONICAL_NOT_IN_REGISTRY"
    target.canonical_artist_id = verified_cid

    # onboard a registry-backed canonical into the existing demand pipeline (idempotent).
    if verified_cid:
        universe.onboard_artist(
            db, canonical_artist_id=verified_cid, display_name=target.display_name,
            discovery_source=SRC_OPERATOR, source_id=target.id, evidence_class=None,
            priority=target.priority, scheduler=scheduler, now=now)

    # apply a YouTube hint only once a registry-backed canonical exists (authoritative channels.list check).
    if verified_cid and target.youtube_hint:
        ref = parse_youtube_hint(target.youtube_hint)
        if ref is None:
            detail["youtube_hint"] = {"status": "UNPARSEABLE", "raw": target.youtube_hint}
        else:
            out = await svc.resolve_youtube_from_hint(
                db, verified_cid, ref=ref, display_name=target.display_name)
            detail["youtube_hint"] = {"status": out.get("status"), "reason": out.get("reason"),
                                      "provider_id": out.get("provider_id"), "lookup": out.get("lookup")}
            if out.get("status") == YT_RESOLVED and out.get("provider_id"):
                target.youtube_channel_id = out["provider_id"]

    target.resolution_method = method
    target.detail = detail
    target.last_resolved_at = now
    # registry-backed canonical → WATCHING/AMBIGUOUS (from live identity state); otherwise it was attempted
    # but lacks a registry-backed canonical → RESOLUTION_PENDING (never NEW again, never fabricated).
    target.status = effective_status(db, target) if verified_cid else RESOLUTION_PENDING
    target.updated_at = now
    # keep the dedup key aligned with a newly-resolved canonical so re-intake stays idempotent.
    new_key = _dedup_key(display_name=target.display_name,
                         canonical_artist_id=target.canonical_artist_id,
                         youtube_channel_id=target.youtube_channel_id)
    if new_key != target.dedup_key and not _dedup_collision(db, new_key, target.id):
        target.dedup_key = new_key
    db.flush()
    return {"target_id": target.id, "status": target.status,
            "canonical_artist_id": target.canonical_artist_id, "resolution_method": method,
            "detail": detail}


def _dedup_collision(db: Session, key: str, self_id: str) -> bool:
    row = db.execute(
        select(ArtistWatchTarget.id).where(ArtistWatchTarget.dedup_key == key)).scalar_one_or_none()
    return row is not None and row != self_id


async def add_and_resolve(db: Session, *, display_name: str, created_by: str,
                          canonical_artist_id: str | None = None, youtube_hint: str | None = None,
                          source: str = "OPERATOR", reason: str | None = None,
                          priority: int | None = None,
                          crawl: CrawlServiceClient | None = None,
                          scheduler: DemandScheduler | None = None,
                          svc: DemandService | None = None,
                          now: datetime | None = None) -> dict[str, Any]:
    """The primary intake path: create the target then attempt resolution inline. A resolution failure
    (a downstream outage) leaves the target NEW/pending — intake itself never fails on it."""
    target, created = create_target(
        db, display_name=display_name, created_by=created_by, canonical_artist_id=canonical_artist_id,
        youtube_hint=youtube_hint, source=source, reason=reason, priority=priority, now=now)
    try:
        await resolve_target(db, target, crawl=crawl, scheduler=scheduler, svc=svc, now=now)
    except Exception as exc:  # noqa: BLE001 — resolution is best-effort; the target persists for retry
        detail = dict(target.detail or {})
        detail["resolution_error"] = str(exc)[:300]
        target.detail = detail
        db.flush()
    return {"created": created, "target": serialize(db, target)}


# ---- operator research-configuration mutations -------------------------------------------------
def pause_target(db: Session, target: ArtistWatchTarget, *, now: datetime | None = None) -> dict[str, Any]:
    target.status = PAUSED
    target.updated_at = now or _now()
    db.flush()
    return serialize(db, target)


async def resume_target(db: Session, target: ArtistWatchTarget, *,
                        crawl: CrawlServiceClient | None = None,
                        scheduler: DemandScheduler | None = None,
                        svc: DemandService | None = None,
                        now: datetime | None = None) -> dict[str, Any]:
    """Resume: recompute the live status (re-resolving if still without a canonical)."""
    now = now or _now()
    target.status = NEW  # clear the operator override so effective_status recomputes
    target.updated_at = now
    db.flush()
    if not target.canonical_artist_id:
        await resolve_target(db, target, crawl=crawl, scheduler=scheduler, svc=svc, now=now)
    else:
        target.status = effective_status(db, target)
        db.flush()
    return serialize(db, target)


def set_priority(db: Session, target: ArtistWatchTarget, priority: int, *,
                 now: datetime | None = None) -> dict[str, Any]:
    target.priority = priority
    target.updated_at = now or _now()
    db.flush()
    return serialize(db, target)


def reject_target(db: Session, target: ArtistWatchTarget, *, reason: str | None = None,
                  now: datetime | None = None) -> dict[str, Any]:
    target.status = REJECTED
    if reason:
        target.reason = reason
    target.updated_at = now or _now()
    db.flush()
    return serialize(db, target)


# ---- bulk intake -------------------------------------------------------------------------------
def parse_bulk_names(text: str) -> list[str]:
    """Split a bulk paste into a bounded, de-duplicated, order-preserving list of names."""
    seen: set[str] = set()
    out: list[str] = []
    for line in re.split(r"[\r\n]+", text or ""):
        name = line.strip()
        if not name:
            continue
        k = _norm(name)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(name)
    return out[:settings.watchlist_bulk_max]


async def preview_bulk(db: Session, names: list[str], *,
                       crawl: CrawlServiceClient | None = None) -> dict[str, Any]:
    """Preview a bulk intake WITHOUT writing: for each name show whether it already has a watch target,
    or matches an existing canonical artist, or would be created new."""
    crawl = crawl or CrawlServiceClient()
    items: list[dict[str, Any]] = []
    for name in names:
        norm = _norm(name)
        existing = db.execute(
            select(ArtistWatchTarget).where(
                ArtistWatchTarget.dedup_key == f"name:{norm}")).scalar_one_or_none()
        disposition = "NEW"
        canonical = None
        if existing is not None or db.execute(
                select(ArtistWatchTarget.id).where(
                    ArtistWatchTarget.canonical_artist_id.is_not(None),
                    func.lower(ArtistWatchTarget.display_name) == name.lower())).first():
            disposition = "DUPLICATE"
        else:
            try:
                match = await crawl.find_artist_by_name(name)
            except Exception:  # noqa: BLE001 — a crawl outage must not break the preview
                match = None
            if match:
                disposition = "MATCHES_CANONICAL"
                canonical = match.get("canonical_entity_id")
        items.append({"display_name": name, "disposition": disposition,
                      "canonical_artist_id": canonical})
    return {"count": len(items),
            "new": sum(1 for i in items if i["disposition"] == "NEW"),
            "duplicates": sum(1 for i in items if i["disposition"] == "DUPLICATE"),
            "matches_canonical": sum(1 for i in items if i["disposition"] == "MATCHES_CANONICAL"),
            "items": items}


async def add_bulk(db: Session, names: list[str], *, created_by: str, source: str = "OPERATOR_BULK",
                   reason: str | None = None, crawl: CrawlServiceClient | None = None,
                   scheduler: DemandScheduler | None = None, svc: DemandService | None = None,
                   now: datetime | None = None) -> dict[str, Any]:
    """Create + resolve watch targets for a bounded batch of names. Idempotent — an existing target for a
    name/canonical is returned, never duplicated."""
    crawl = crawl or CrawlServiceClient()
    scheduler = scheduler or DemandScheduler()
    svc = svc or DemandService()
    now = now or _now()
    results = []
    created_count = 0
    for name in names[:settings.watchlist_bulk_max]:
        out = await add_and_resolve(db, display_name=name, created_by=created_by, source=source,
                                    reason=reason, crawl=crawl, scheduler=scheduler, svc=svc, now=now)
        created_count += 1 if out["created"] else 0
        results.append(out["target"])
    return {"received": len(names), "created": created_count,
            "existing": len(results) - created_count, "targets": results}


# ---- diagnostics -------------------------------------------------------------------------------
def diagnostics(db: Session) -> dict[str, Any]:
    """Watchlist coverage — part of Demand Intelligence coverage. Live counts (effective status)."""
    targets = db.execute(select(ArtistWatchTarget)).scalars().all()
    counts = {WATCHING: 0, RESOLUTION_PENDING: 0, AMBIGUOUS: 0, PAUSED: 0, REJECTED: 0, NEW: 0}
    with_canonical = 0
    with_verified_youtube = 0
    receiving_demand = 0
    for t in targets:
        st = effective_status(db, t)
        counts[st] = counts.get(st, 0) + 1
        if t.canonical_artist_id:
            with_canonical += 1
            if youtube_identity_state(db, t.canonical_artist_id) == "RESOLVED":
                with_verified_youtube += 1
            from artist_intelligence_service.models import ArtistDemandObservation as _ADO
            has_obs = db.execute(
                select(_ADO.id).where(_ADO.canonical_artist_id == t.canonical_artist_id).limit(1)
            ).scalar_one_or_none() is not None
            if has_obs:
                receiving_demand += 1
    return {
        "total": len(targets),
        "watching": counts[WATCHING],
        "resolution_pending": counts[RESOLUTION_PENDING],
        "ambiguous": counts[AMBIGUOUS],
        "paused": counts[PAUSED],
        "rejected": counts[REJECTED],
        "new": counts[NEW],
        "targets_with_canonical_artist": with_canonical,
        "targets_with_verified_youtube_identity": with_verified_youtube,
        "targets_receiving_demand_observations": receiving_demand,
    }


async def canonical_integrity(db: Session, *, crawl: CrawlServiceClient | None = None) -> dict[str, Any]:
    """Audit every artist-intel canonical reference against the authoritative crawl registry (5B.1.1).

    Read-only. Reports ids referenced by watch targets / candidates / external identities / demand
    observations that the crawl entity-resolution registry does NOT acknowledge — orphans. After 5B.1.1
    the watchlist only ever exposes registry-backed canonicals, so `watch_targets.orphans` should stay
    empty; the rest is surfaced for ongoing auditability and owner-side reconciliation, never rewritten."""
    from artist_intelligence_service.models import (
        ArtistCandidate,
        ArtistDemandObservation,
        ArtistExternalIdentity,
    )
    crawl = crawl or CrawlServiceClient()
    registry: set[str] = set()
    available = True
    try:
        offset = 0
        for _ in range(20):  # bounded pages
            rows = await crawl.artists(limit=200, offset=offset)
            if not rows:
                break
            for r in rows:
                cid = r.get("canonical_entity_id") or r.get("canonical_artist_id") or r.get("id")
                if cid:
                    registry.add(cid)
            if len(rows) < 200:
                break
            offset += 200
    except Exception:  # noqa: BLE001 — a crawl outage degrades safely (cannot audit right now)
        available = False

    def _refs(col) -> set[str]:
        return set(db.execute(select(func.distinct(col)).where(col.is_not(None))).scalars())

    wt = _refs(ArtistWatchTarget.canonical_artist_id)
    cd = _refs(ArtistCandidate.canonical_artist_id)
    idn = set(db.execute(select(func.distinct(ArtistExternalIdentity.canonical_artist_id))).scalars())
    ob = set(db.execute(select(func.distinct(ArtistDemandObservation.canonical_artist_id))).scalars())

    def _orphans(s: set[str]) -> list[str]:
        return sorted(s - registry) if available else []

    all_orphans = set(_orphans(wt)) | set(_orphans(cd)) | set(_orphans(idn)) | set(_orphans(ob))
    return {
        "registry_available": available,
        "registry_canonical_artists": len(registry) if available else None,
        "watch_targets": {"referenced": len(wt), "orphans": _orphans(wt)},
        "candidates": {"referenced": len(cd), "orphans": _orphans(cd)},
        "external_identities": {"referenced": len(idn), "orphans": _orphans(idn)},
        "demand_observations": {"referenced": len(ob), "orphans": _orphans(ob)},
        "orphan_total": len(all_orphans),
        "note": "canonical ids referenced by artist-intelligence but not acknowledged by the crawl "
                "registry; auditable, never silently rewritten (Phase 5B.1.1).",
    }


async def reresolve_pending(db: Session, *, limit: int | None = None,
                            crawl: CrawlServiceClient | None = None,
                            scheduler: DemandScheduler | None = None,
                            svc: DemandService | None = None,
                            now: datetime | None = None) -> dict[str, Any]:
    """Bounded re-resolution pass over still-pending targets (a name may have accrued canonical evidence
    since intake). Persisted work — never the whole backlog synchronously."""
    limit = limit or settings.watchlist_reresolve_batch_size
    rows = db.execute(
        select(ArtistWatchTarget).where(ArtistWatchTarget.status.in_((NEW, RESOLUTION_PENDING)))
        .order_by(ArtistWatchTarget.created_at).limit(limit)).scalars().all()
    resolved = 0
    for t in rows:
        out = await resolve_target(db, t, crawl=crawl, scheduler=scheduler, svc=svc, now=now)
        if out.get("status") in (WATCHING, AMBIGUOUS):
            resolved += 1
    return {"examined": len(rows), "now_resolved": resolved, "batch_limit": limit}
