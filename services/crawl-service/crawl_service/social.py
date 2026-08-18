"""Social evidence foundation service (Phase 5C.1).

The governed spine: associate public social identities with existing canonical entities, drive
**watchlist-bounded** acquisition through signal-service, and persist durable ``SocialMention``
evidence. It never creates a canonical Event, never persists raw captions/media (only extracted claims
+ provenance + a content hash), and represents absent authorized access honestly (deferred /
access-pending) — never a fabricated failure and never a scrape fallback.

Service boundary mirrors the ticketing spine: crawl-service scheduler → (HTTP) signal-service. Canonical
ownership stays with the registry/resolution layer; this module only associates + collects evidence.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select

from crawl_service.config import Settings, settings
from crawl_service.models import SocialIdentity, SocialMention

_ENTITY_TYPES = ("ARTIST", "VENUE", "ORGANIZER")
_COLLECTIBLE = ("PRODUCTION", "MOCK")
_ACCESS_PENDING_STATES = ("ACCESS_PENDING", "CREDENTIAL_UNAVAILABLE", "DISABLED", "UNSUPPORTED")


def _uuid() -> str:
    return uuid.uuid4().hex


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class SocialAcquisitionService:
    def __init__(self, session_factory, config: Settings | None = None,
                 http_client_factory=None) -> None:
        self._sf = session_factory
        self._cfg = config or settings
        # injectable for tests (a stub that returns a SocialCollectionResult-shaped dict)
        self._http_factory = http_client_factory

    # ---- identity association (governed, auditable) ---------------------------------------------
    def link_identity(self, *, canonical_entity_id: str, canonical_entity_type: str, platform: str,
                      handle: str, platform_account_id: str | None = None, account_url: str | None = None,
                      evidence_role: str | None = None, source: str = "OPERATOR",
                      actor: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        etype = (canonical_entity_type or "").upper()
        if etype not in _ENTITY_TYPES:
            raise ValueError(f"canonical_entity_type must be one of {_ENTITY_TYPES}")
        plat = (platform or "").upper()
        if plat not in ("INSTAGRAM", "FACEBOOK", "REDDIT"):
            raise ValueError("platform must be INSTAGRAM, FACEBOOK or REDDIT")
        handle = handle.strip()
        if not handle or not canonical_entity_id:
            raise ValueError("canonical_entity_id and handle are required")
        role = (evidence_role or ("COMMUNITY_EVIDENCE" if plat == "REDDIT" else "OFFICIAL_ACCOUNT_EVIDENCE")).upper()
        with self._sf() as s, s.begin():
            row = s.execute(select(SocialIdentity).where(
                SocialIdentity.canonical_entity_id == canonical_entity_id,
                SocialIdentity.platform == plat, SocialIdentity.handle == handle)).scalar_one_or_none()
            created = row is None
            if row is None:
                row = SocialIdentity(
                    id=_uuid(), canonical_entity_id=canonical_entity_id, canonical_entity_type=etype,
                    platform=plat, handle=handle, first_seen_at=now, created_at=now, updated_at=now,
                    provenance={"history": []})
                s.add(row)
            row.platform_account_id = platform_account_id or row.platform_account_id
            row.account_url = account_url or row.account_url
            row.evidence_role = role
            row.canonical_entity_type = etype
            row.source = source
            row.active = True
            if row.collection_state == "DISABLED":
                row.collection_state = "ELIGIBLE"
            prov = dict(row.provenance or {})
            prov.setdefault("history", []).append(
                {"action": "LINK" if created else "UPDATE", "actor": actor or source, "at": now.isoformat()})
            row.provenance = prov
            row.updated_at = now
            rid = row.id
        return {"id": rid, "created": created, "canonical_entity_id": canonical_entity_id,
                "canonical_entity_type": etype, "platform": plat, "handle": handle,
                "evidence_role": role, "verification_state": "ASSERTED", "audited": True}

    def set_active(self, identity_id: str, *, active: bool, actor: str | None = None,
                   now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        with self._sf() as s, s.begin():
            row = s.get(SocialIdentity, identity_id)
            if row is None:
                raise ValueError("identity not found")
            row.active = active
            if not active:
                row.collection_state = "DISABLED"
            elif row.collection_state == "DISABLED":
                row.collection_state = "ELIGIBLE"
            prov = dict(row.provenance or {})
            prov.setdefault("history", []).append(
                {"action": "ACTIVATE" if active else "DEACTIVATE", "actor": actor, "at": now.isoformat()})
            row.provenance = prov
            row.updated_at = now
        return {"id": identity_id, "active": active}

    # ---- watchlist eligibility -------------------------------------------------------------------
    def _eligible_query(self, now: datetime):
        return (select(SocialIdentity)
                .where(SocialIdentity.active.is_(True),
                       SocialIdentity.platform.in_(tuple(self._cfg.social_platform_set) or ("",)),
                       SocialIdentity.collection_state != "DISABLED",
                       ((SocialIdentity.next_eligible_at.is_(None))
                        | (SocialIdentity.next_eligible_at <= now)))
                .order_by(SocialIdentity.next_eligible_at.is_(None).desc(),
                          SocialIdentity.next_eligible_at.asc()))

    def watchlist(self, *, limit: int = 100) -> dict[str, Any]:
        now = datetime.now(UTC)
        with self._sf() as s:
            eligible = s.execute(self._eligible_query(now).limit(limit)).scalars().all()
            total = int(s.execute(select(func.count()).select_from(SocialIdentity)
                                  .where(SocialIdentity.active.is_(True))).scalar_one())
        return {"active_identities": total, "eligible_now": len(eligible),
                "platforms_enabled": sorted(self._cfg.social_platform_set),
                "items": [self._identity_summary(r) for r in eligible]}

    # ---- acquisition run -------------------------------------------------------------------------
    async def run_once(self, *, worker_id: str = "social", now: datetime | None = None,
                       limit: int | None = None, trace: bool = False) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        limit = limit or self._cfg.social_max_identities_per_run
        with self._sf() as s:
            rows = s.execute(self._eligible_query(now).limit(limit)).scalars().all()
            work = [(r.id, r.platform, r.handle, r.evidence_role) for r in rows]

        summary = {"processed": 0, "collected": 0, "deferred": 0, "failed": 0,
                   "mentions_new": 0, "mentions_updated": 0, "mentions_unchanged": 0,
                   "by_access_state": {}, "worker_id": worker_id}
        traces: list[dict] = []
        for identity_id, platform, handle, role in work:
            summary["processed"] += 1
            try:
                result = await self._collect(platform, handle, role)
            except Exception as exc:  # noqa: BLE001 — a transient collection error never corrupts identity
                summary["failed"] += 1
                self._record_failure(identity_id, str(exc), now)
                if trace:
                    traces.append({"identity": identity_id, "platform": platform, "handle": handle,
                                   "outcome": "COLLECT_ERROR", "error": str(exc)})
                continue
            state = result.get("access_state", "UNKNOWN")
            summary["by_access_state"][state] = summary["by_access_state"].get(state, 0) + 1
            if not result.get("collectible"):
                summary["deferred"] += 1
                self._record_deferral(identity_id, state, now)
                if trace:
                    traces.append({"identity": identity_id, "platform": platform, "handle": handle,
                                   "outcome": "DEFERRED", "access_state": state})
                continue
            ing = self._ingest_mentions(identity_id, result.get("mentions", []), now)
            summary["collected"] += 1
            summary["mentions_new"] += ing["new"]
            summary["mentions_updated"] += ing["updated"]
            summary["mentions_unchanged"] += ing["unchanged"]
            self._record_success(identity_id, state, bool(result.get("mentions")), now)
            if trace:
                traces.append({"identity": identity_id, "platform": platform, "handle": handle,
                               "outcome": "COLLECTED", "access_state": state, **ing})
        if trace:
            summary["trace"] = traces
        return summary

    async def _collect(self, platform: str, handle: str, role: str) -> dict[str, Any]:
        """POST to signal-service /social/collect. Returns the SocialCollectionResult dict."""
        if self._http_factory is not None:
            return await self._http_factory(platform=platform, account=handle, evidence_role=role)
        url = f"{self._cfg.signal_service_url}/v1/signals/social/collect"
        payload = {"platform": platform, "account": handle, "evidence_role": role}
        async with httpx.AsyncClient(timeout=self._cfg.capture_http_timeout_seconds) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()

    # ---- persistence (append-only, immutable versions) -------------------------------------------
    def _ingest_mentions(self, identity_id: str, mentions: list[dict], now: datetime) -> dict[str, int]:
        """Persist collected mentions as IMMUTABLE evidence versions.

        A logical post is ``(platform, platform_post_id)``. First observation → version 1. A later
        observation with a **changed** ``content_hash`` INSERTS a new version and flips the prior
        version's ``is_current`` pointer (its evidence fields are never rewritten). A recapture with the
        same hash is idempotent. So an earlier observed state is never erased by a later one.

        Returns counters (keys kept stable for the run summary): ``new`` = new logical posts (version 1),
        ``updated`` = new immutable versions appended for changed posts, ``unchanged`` = idempotent
        recaptures.
        """
        new = versioned = unchanged = 0
        with self._sf() as s, s.begin():
            for m in mentions:
                platform = str(m.get("platform", "")).upper()
                post_id = str(m.get("platform_post_id", ""))
                if not platform or not post_id:
                    continue
                content_hash = m.get("content_hash")
                current = s.execute(select(SocialMention).where(
                    SocialMention.platform == platform,
                    SocialMention.platform_post_id == post_id,
                    SocialMention.is_current.is_(True))).scalar_one_or_none()
                # Idempotent: an unchanged recapture of the current version is a no-op (no mutation).
                if current is not None and _same_state(current, content_hash, m.get("extracted_claims")):
                    unchanged += 1
                    continue
                # New logical post, or a materially changed one → append a new immutable version.
                prior_id = current.id if current is not None else None
                next_version = (current.version + 1) if current is not None else 1
                self._append_version(s, identity_id, m, platform, post_id, content_hash,
                                     version=next_version, previous_id=prior_id, now=now)
                if current is not None:
                    # only the pointer/bookkeeping change on the prior row — evidence stays immutable.
                    current.is_current = False
                    current.superseded_at = now
                    current.updated_at = now
                    versioned += 1
                else:
                    new += 1
        return {"new": new, "updated": versioned, "unchanged": unchanged}

    def _append_version(self, s, identity_id: str, m: dict, platform: str, post_id: str,
                        content_hash, *, version: int, previous_id: str | None, now: datetime) -> None:
        # NEVER persist raw expressive content: only the extracted claims, hash and provenance the
        # adapter returns. A caption/media field is not read here even if present.
        ident = s.get(SocialIdentity, identity_id)
        s.add(SocialMention(
            id=_uuid(), platform=platform, source_account=str(m.get("source_account", "")),
            social_identity_id=identity_id, platform_post_id=post_id,
            post_url=m.get("post_url"), published_at=_parse_dt(m.get("published_at")),
            observed_at=_parse_dt(m.get("observed_at")) or now,
            canonical_entity_id=ident.canonical_entity_id if ident else None,
            canonical_entity_type=ident.canonical_entity_type if ident else None,
            linked_canonical_entity_ids=list(m.get("linked_handles") or []),
            extracted_claims=m.get("extracted_claims") or {},
            evidence_role=str(m.get("evidence_role") or "SOCIAL_DISCOVERY"),
            confidence=float(m.get("confidence") or 0.5),
            parser_version=str(m.get("parser_version") or "social-1"),
            content_hash=content_hash, provenance=m.get("provenance") or {},
            version=version, is_current=True, previous_mention_id=previous_id,
            processing_status="UNPROCESSED", created_at=now, updated_at=now))

    def _record_success(self, identity_id: str, access_state: str, observed: bool, now: datetime) -> None:
        with self._sf() as s, s.begin():
            row = s.get(SocialIdentity, identity_id)
            if row is None:
                return
            row.collection_state = "ELIGIBLE"
            row.consecutive_failures = 0
            row.last_collected_at = now
            row.last_access_state = access_state
            if observed:
                row.last_observed_at = now
            row.next_eligible_at = now + timedelta(seconds=self._cfg.social_collection_interval_seconds)
            row.updated_at = now

    def _record_deferral(self, identity_id: str, access_state: str, now: datetime) -> None:
        with self._sf() as s, s.begin():
            row = s.get(SocialIdentity, identity_id)
            if row is None:
                return
            # honest access-pending — NOT a failure; identity untouched except scheduling state
            row.collection_state = "ACCESS_PENDING" if access_state in _ACCESS_PENDING_STATES else "DEFERRED"
            row.last_access_state = access_state
            row.next_eligible_at = now + timedelta(seconds=self._cfg.social_access_pending_retry_seconds)
            row.updated_at = now

    def _record_failure(self, identity_id: str, error: str, now: datetime) -> None:
        with self._sf() as s, s.begin():
            row = s.get(SocialIdentity, identity_id)
            if row is None:
                return
            row.consecutive_failures = (row.consecutive_failures or 0) + 1
            row.collection_state = "DEFERRED"
            row.last_access_state = "COLLECT_ERROR"
            backoff = self._cfg.social_backoff_seconds * min(row.consecutive_failures, 6)
            row.next_eligible_at = now + timedelta(seconds=backoff)
            prov = dict(row.provenance or {})
            prov.setdefault("errors", []).append({"error": error[:200], "at": now.isoformat()})
            row.provenance = prov
            row.updated_at = now

    # ---- reads / diagnostics ---------------------------------------------------------------------
    def identities(self, *, canonical_entity_id: str | None = None, platform: str | None = None,
                   limit: int = 200) -> dict[str, Any]:
        with self._sf() as s:
            stmt = select(SocialIdentity)
            if canonical_entity_id:
                stmt = stmt.where(SocialIdentity.canonical_entity_id == canonical_entity_id)
            if platform:
                stmt = stmt.where(SocialIdentity.platform == platform.upper())
            rows = s.execute(stmt.order_by(SocialIdentity.canonical_entity_id).limit(limit)).scalars().all()
        return {"count": len(rows), "items": [self._identity_summary(r) for r in rows]}

    def mentions(self, *, canonical_entity_id: str | None = None, platform: str | None = None,
                 processing_status: str | None = None, current_only: bool = True,
                 limit: int = 100) -> dict[str, Any]:
        """List social evidence. By default only the CURRENT version of each logical post (compact);
        ``current_only=False`` returns every immutable version. Claims + provenance only — no raw content."""
        with self._sf() as s:
            stmt = select(SocialMention)
            if current_only:
                stmt = stmt.where(SocialMention.is_current.is_(True))
            if canonical_entity_id:
                stmt = stmt.where(SocialMention.canonical_entity_id == canonical_entity_id)
            if platform:
                stmt = stmt.where(SocialMention.platform == platform.upper())
            if processing_status:
                stmt = stmt.where(SocialMention.processing_status == processing_status)
            rows = s.execute(stmt.order_by(SocialMention.observed_at.desc()).limit(limit)).scalars().all()
        return {"count": len(rows), "current_only": current_only,
                "items": [self._mention_summary(r) for r in rows]}

    def mention_history(self, *, platform: str, platform_post_id: str) -> dict[str, Any]:
        """Full immutable version lineage of one logical post — for operator inspection of source change.

        Exposes, per version: version number, observed time, content hash, extracted claims, confidence,
        previous-version lineage and current-flag. No raw expressive source content is exposed."""
        plat = (platform or "").upper()
        with self._sf() as s:
            rows = s.execute(select(SocialMention).where(
                SocialMention.platform == plat,
                SocialMention.platform_post_id == platform_post_id)
                .order_by(SocialMention.version.asc())).scalars().all()
        if not rows:
            return {"platform": plat, "platform_post_id": platform_post_id, "versions": 0, "items": []}
        observed = [_iso(_aware(r.observed_at)) for r in rows]
        current = next((r for r in rows if r.is_current), rows[-1])
        return {
            "platform": plat, "platform_post_id": platform_post_id,
            "post_url": current.post_url, "source_account": current.source_account,
            "canonical_entity_id": current.canonical_entity_id,
            "canonical_entity_type": current.canonical_entity_type,
            "versions": len(rows), "revised": len(rows) > 1,
            "first_observed_at": observed[0], "latest_observed_at": _iso(_aware(current.observed_at)),
            "content_hashes": [r.content_hash for r in rows],
            "items": [{
                "id": r.id, "version": r.version, "is_current": r.is_current,
                "observed_at": _iso(_aware(r.observed_at)),
                "superseded_at": _iso(_aware(r.superseded_at)),
                "content_hash": r.content_hash, "extracted_claims": r.extracted_claims,
                "confidence": r.confidence, "parser_version": r.parser_version,
                "previous_mention_id": r.previous_mention_id,
                "processing_status": r.processing_status, "claim_type": r.claim_type,
            } for r in rows],
            "note": "Immutable observed versions of one social post — each materially changed source "
                    "state is preserved; earlier observations are never erased. No raw content.",
        }

    def coverage(self) -> dict[str, Any]:
        with self._sf() as s:
            ids = s.execute(select(SocialIdentity)).scalars().all()
            mentions = s.execute(select(SocialMention)).scalars().all()
        def _mp() -> dict[str, Any]:
            return {"identities": 0, "active": 0, "eligible": 0, "access_pending": 0, "deferred": 0,
                    "canonical_entities": set(), "last_access_state": None, "last_collected_at": None,
                    "mentions": 0, "versions": 0, "revised": 0, "unprocessed_mentions": 0}
        by_platform: dict[str, dict[str, Any]] = {}
        for r in ids:
            p = by_platform.setdefault(r.platform, _mp())
            p["identities"] += 1
            p["active"] += int(r.active)
            if r.active and r.collection_state == "ELIGIBLE":
                p["eligible"] += 1
            if r.collection_state == "ACCESS_PENDING":
                p["access_pending"] += 1
            if r.collection_state == "DEFERRED":
                p["deferred"] += 1
            p["canonical_entities"].add(r.canonical_entity_id)
            if r.last_access_state:
                p["last_access_state"] = r.last_access_state
            lc = _iso(_aware(r.last_collected_at))
            if lc and (p["last_collected_at"] is None or lc > p["last_collected_at"]):
                p["last_collected_at"] = lc
        for m in mentions:
            p = by_platform.setdefault(m.platform, _mp())
            p["versions"] += 1                                    # every immutable evidence version
            if m.is_current:
                p["mentions"] += 1                                # logical posts = current versions
                if m.version > 1:
                    p["revised"] += 1                             # post changed since first observed
                p["unprocessed_mentions"] += int(m.processing_status == "UNPROCESSED")
        for p in by_platform.values():
            p["canonical_entities"] = len(p["canonical_entities"])
        current = [m for m in mentions if m.is_current]
        return {
            "social_enabled": self._cfg.social_enabled,
            "platforms_enabled": sorted(self._cfg.social_platform_set),
            "total_identities": len(ids),
            "total_mentions": len(current),                       # logical posts (current versions)
            "total_mention_versions": len(mentions),              # all immutable evidence versions
            "revised_posts": sum(1 for m in current if m.version > 1),
            "unresolved_mentions": sum(1 for m in current if not m.canonical_entity_id),
            "by_platform": by_platform,
            "note": "Social evidence coverage. A logical post is versioned append-only: each changed "
                    "source state is a new immutable version, and 'total_mentions' counts current "
                    "versions. Mentions are evidence only — no SocialMention creates a canonical Event. "
                    "Access state is the honest acquisition posture; raw post content is not retained "
                    "(claims + provenance only).",
        }

    @staticmethod
    def _identity_summary(r: SocialIdentity) -> dict[str, Any]:
        return {"id": r.id, "canonical_entity_id": r.canonical_entity_id,
                "canonical_entity_type": r.canonical_entity_type, "platform": r.platform,
                "handle": r.handle, "platform_account_id": r.platform_account_id,
                "account_url": r.account_url, "evidence_role": r.evidence_role,
                "verification_state": r.verification_state, "active": r.active,
                "collection_state": r.collection_state, "consecutive_failures": r.consecutive_failures,
                "last_access_state": r.last_access_state,
                "last_collected_at": _iso(_aware(r.last_collected_at)),
                "next_eligible_at": _iso(_aware(r.next_eligible_at)), "source": r.source}

    @staticmethod
    def _mention_summary(r: SocialMention) -> dict[str, Any]:
        return {"id": r.id, "platform": r.platform, "source_account": r.source_account,
                "platform_post_id": r.platform_post_id, "post_url": r.post_url,
                "published_at": _iso(_aware(r.published_at)), "observed_at": _iso(_aware(r.observed_at)),
                "canonical_entity_id": r.canonical_entity_id,
                "canonical_entity_type": r.canonical_entity_type,
                "linked_canonical_entity_ids": r.linked_canonical_entity_ids,
                "extracted_claims": r.extracted_claims, "evidence_role": r.evidence_role,
                "confidence": r.confidence, "parser_version": r.parser_version,
                "content_hash": r.content_hash, "processing_status": r.processing_status,
                "claim_type": r.claim_type, "provenance": r.provenance,
                "version": r.version, "is_current": r.is_current,
                "previous_mention_id": r.previous_mention_id,
                "superseded_at": _iso(_aware(r.superseded_at))}


def _same_state(current: SocialMention, incoming_hash, incoming_claims) -> bool:
    """Is an incoming capture the SAME observed state as the current version (→ idempotent no-op)?

    Prefer the provider content hash. When a hash is absent on either side (robustness — we never assume
    it is present), fall back to a deterministic comparison of the extracted claims so a genuine change
    still produces a new immutable version and an identical recapture stays idempotent.
    """
    if current.content_hash and incoming_hash:
        return current.content_hash == incoming_hash
    return json.dumps(current.extracted_claims or {}, sort_keys=True, default=str) == \
        json.dumps(incoming_claims or {}, sort_keys=True, default=str)


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None
