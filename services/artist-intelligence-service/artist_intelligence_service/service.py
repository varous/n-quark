"""DemandService — orchestrates acquisition (via signal-service) + persistence into the demand ledger.

Guarantees:
- provider resolution NEVER creates a canonical artist (identity only attaches to an existing one);
- observation ingest is idempotent on ``observation_key`` (no duplicate logical observations);
- history is preserved (a new day's snapshot / a new export is new history, never an overwrite);
- YouTube quota is accounted per provider/day, and search is refused past the daily budget;
- one provider/artist failure is isolated by the caller (scheduler) — this layer surfaces errors.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from artist_intelligence_service import identity as idlib
from artist_intelligence_service.config import settings
from artist_intelligence_service.models import ArtistDemandObservation, ArtistExternalIdentity
from artist_intelligence_service.providers import youtube as ytprov
from artist_intelligence_service.providers.base import (
    AMBIGUOUS,
    PROVIDER_GOOGLE_TRENDS,
    PROVIDER_YOUTUBE,
    RESOLVED,
    UNRESOLVED,
    DemandDatum,
)
from artist_intelligence_service.providers.google_trends import (
    GoogleTrendsImportProvider,
    GoogleTrendsProvider,
    parse_trends_csv,
)
from artist_intelligence_service.providers.youtube import YouTubeProvider
from artist_intelligence_service.quota import (
    BUCKET_GENERAL_READ,
    BUCKET_SEARCH,
    YT_SEARCH_UNITS,
    QuotaMeter,
    can_spend,
    record_meter,
    search_calls_today,
)


class QuotaExhausted(RuntimeError):
    """The daily search budget is spent; identity discovery is refused until it resets."""


def _now() -> datetime:
    return datetime.now(UTC)


def yt_observation_bucket(observed_at: datetime) -> str:
    """Idempotency bucket for a YouTube live-metric observation. Phase 5A.3 raises the temporal
    resolution to HOURLY (``YYYY-MM-DDTHH``) when enabled — a same-hour rerun is one logical
    observation, the next hour is new history. Old DAILY records stay valid (different key)."""
    if settings.youtube_hourly_observations:
        return observed_at.strftime("%Y-%m-%dT%H")
    return observed_at.date().isoformat()


class DemandService:
    def __init__(self, *, youtube: YouTubeProvider | None = None,
                 trends_import: GoogleTrendsImportProvider | None = None,
                 trends_official: GoogleTrendsProvider | None = None) -> None:
        self.youtube = youtube or YouTubeProvider()
        self.trends_import = trends_import or GoogleTrendsImportProvider()
        self.trends_official = trends_official or GoogleTrendsProvider()

    # ---- identity ----------------------------------------------------------------------------
    def get_identity(self, db: Session, provider: str, identity_type: str,
                     provider_id: str) -> ArtistExternalIdentity | None:
        return db.execute(
            select(ArtistExternalIdentity).where(
                ArtistExternalIdentity.provider == provider,
                ArtistExternalIdentity.identity_type == identity_type,
                ArtistExternalIdentity.provider_id == provider_id,
            )
        ).scalar_one_or_none()

    def list_identities(self, db: Session, canonical_artist_id: str) -> list[ArtistExternalIdentity]:
        return list(db.execute(
            select(ArtistExternalIdentity)
            .where(ArtistExternalIdentity.canonical_artist_id == canonical_artist_id)
            .order_by(ArtistExternalIdentity.provider, ArtistExternalIdentity.identity_type)
        ).scalars())

    def _upsert_identity(self, db: Session, *, canonical_artist_id: str, provider: str,
                         identity_type: str, provider_id: str, status: str, display_name: str | None,
                         canonical_url: str | None, resolution_method: str | None, confidence: float,
                         metadata: dict[str, Any], provenance: dict[str, Any] | None = None,
                         last_verified_at: datetime | None = None) -> ArtistExternalIdentity:
        now = _now()
        row = self.get_identity(db, provider, identity_type, provider_id)
        if row is None:
            row = ArtistExternalIdentity(
                id=idlib.new_id(provider, identity_type, provider_id),
                canonical_artist_id=canonical_artist_id, provider=provider,
                identity_type=identity_type, provider_id=provider_id, first_seen_at=now,
                created_at=now, updated_at=now, identity_metadata={}, provenance={},
            )
            db.add(row)
        # A provider identity attaches to an existing canonical artist; it never rebinds to another.
        row.canonical_artist_id = canonical_artist_id
        row.status = status
        row.display_name = display_name
        row.canonical_url = canonical_url
        row.resolution_method = resolution_method
        row.confidence = confidence
        row.identity_metadata = metadata
        if provenance is not None:
            row.provenance = provenance
        # last_verified_at means "successfully verified against the provider at this time" — it is
        # populated ONLY by a caller that actually performed provider verification, never by status.
        if last_verified_at is not None:
            row.last_verified_at = last_verified_at
        row.updated_at = now
        db.flush()
        return row

    async def resolve_youtube(self, db: Session, canonical_artist_id: str, *, query: str,
                              hints: dict | None = None, limit: int = 5,
                              search_purpose: str | None = None) -> dict[str, Any]:
        """Bounded search → deterministic decision → AUTHORITATIVE channels.list verification → persist.

        A CHANNEL_ID may become RESOLVED only if channels.list confirms it exists at resolution time
        (Phase 5A.1a): search is candidate discovery only. If the deterministic leader is not found, its
        rejection is recorded and the next ranked candidate is considered — but it must still satisfy
        the deterministic thresholds/ambiguity policy on its own. A transient verification failure never
        resolves and never invalidates. Never creates a canonical artist; `last_verified_at` is set only
        on a successful verification."""
        if not settings.youtube_search_enabled:
            raise QuotaExhausted("YouTube search disabled (NQUARK_YOUTUBE_SEARCH_ENABLED=false)")
        if search_calls_today(db, PROVIDER_YOUTUBE) >= settings.youtube_max_searches_per_day:
            raise QuotaExhausted(
                f"daily YouTube search budget spent ({settings.youtube_max_searches_per_day})")

        meter = QuotaMeter()
        self.youtube.meter = meter
        rejected: list[dict[str, Any]] = []
        verification_unavailable = False
        verified_leader_pool = 0
        try:
            candidates = await self.youtube.search_artist(query, limit=limit, purpose=search_purpose)
            for c in candidates:
                c.score, c.signals = ytprov._score_candidate(query, c, hints or {})
            candidates.sort(key=lambda c: c.score, reverse=True)

            # 5B.2.7 §7/§8 — search is candidate DISCOVERY; provider verification is a SEPARATE step.
            # Authoritatively verify (channels.list) the top-N PLAUSIBLE candidates — not only when
            # search-only scoring already declares a clear leader — so ambiguous-by-snippet artists can
            # still reach the authoritative check. Re-score each verified candidate on its channels.list
            # metadata, then decide among the VERIFIED pool (the margin rule still guards impostors).
            plausible = [c for c in candidates
                         if c.score >= ytprov.AMBIGUOUS_FLOOR][: settings.youtube_verify_top_n]
            verified_cands: list = []
            verified_by_id: dict[str, dict[str, Any]] = {}
            for cand in plausible:
                if not can_spend(db, PROVIDER_YOUTUBE, BUCKET_GENERAL_READ, 1):
                    verification_unavailable = True     # general pool reserve reached → defer, don't resolve
                    break
                try:
                    v = await self.youtube.verify_channel(cand.provider_id)
                except Exception:  # noqa: BLE001 — network/provider failure: cannot verify, cannot resolve
                    verification_unavailable = True
                    break
                if v.get("status") == "FOUND":
                    cand.score, cand.signals = ytprov.score_with_verification(query, cand, v, hints or {})
                    verified_cands.append(cand)
                    verified_by_id[cand.provider_id] = v
                else:
                    rejected.append({"provider_id": cand.provider_id, "score": cand.score,
                                     "signals": cand.signals, "verification_result": "CHANNEL_NOT_FOUND"})
            verified_leader_pool = len(verified_cands)
            chosen = None
            verified = None
            if verified_cands:
                verified_cands.sort(key=lambda c: c.score, reverse=True)
                decision = ytprov._decide(verified_cands)
                if decision.status == RESOLVED and decision.chosen is not None:
                    chosen = decision.chosen
                    verified = verified_by_id.get(chosen.provider_id)
        finally:
            record_meter(db, PROVIDER_YOUTUBE, meter)

        cand_meta = {"query": query, "candidates": [
            {"provider_id": c.provider_id, "display_name": c.display_name, "score": c.score,
             "signals": c.signals, "canonical_url": c.canonical_url} for c in candidates]}

        now = _now()
        if chosen is not None:
            provenance = {"candidate_score": chosen.score, "candidate_signals": chosen.signals,
                          "verification_method": "channels.list", "verified_provider_id": chosen.provider_id,
                          "verified_at": now.isoformat(),
                          "verified_title": verified.get("title"), "verified_handle": verified.get("handle")}
            identity = self._upsert_identity(
                db, canonical_artist_id=canonical_artist_id, provider=PROVIDER_YOUTUBE,
                identity_type="CHANNEL_ID", provider_id=chosen.provider_id, status=RESOLVED,
                display_name=chosen.display_name, canonical_url=chosen.canonical_url,
                resolution_method="deterministic-evidence+channels.list-verified", confidence=chosen.score,
                metadata={**cand_meta, "reason": "verified_clear_leader", "rejected_candidates": rejected},
                provenance=provenance, last_verified_at=now)
            pending = self.get_identity(db, PROVIDER_YOUTUBE, "CHANNEL_ID",
                                        idlib.pending_identity_id(canonical_artist_id))
            if pending is not None and pending.id != identity.id:
                pending.status = "REJECTED"
                pending.updated_at = now
                db.flush()
            return {"canonical_artist_id": canonical_artist_id, "status": RESOLVED,
                    "reason": "verified_clear_leader", "method": identity.resolution_method,
                    "identity_id": identity.id, "provider_id": identity.provider_id,
                    "verified": True, "rejected_candidates": rejected, "candidates": cand_meta["candidates"]}

        # Nothing verified: the reason distinguishes a transient/deferred verification from a genuinely
        # ambiguous authoritative result and from candidates that do not exist at the provider.
        if verification_unavailable:
            status, reason = AMBIGUOUS, "verification_unavailable"
        elif verified_leader_pool > 0:
            # authoritative channels.list succeeded but no single verified channel is a clear leader
            # (e.g. equally-named verified channels) — genuinely ambiguous, not a wiring gap.
            status, reason = AMBIGUOUS, "verified_no_clear_leader"
        elif rejected:
            status, reason = UNRESOLVED, "all_candidates_provider_not_found"
        else:
            decision = ytprov._decide(candidates)  # AMBIGUOUS/UNRESOLVED per the pure policy
            status, reason = decision.status, decision.reason
        identity = self._upsert_identity(
            db, canonical_artist_id=canonical_artist_id, provider=PROVIDER_YOUTUBE,
            identity_type="CHANNEL_ID", provider_id=idlib.pending_identity_id(canonical_artist_id),
            status=status, display_name=None, canonical_url=None,
            resolution_method="deterministic-evidence-v1",
            confidence=(candidates[0].score if candidates else 0.0),
            metadata={**cand_meta, "reason": reason, "rejected_candidates": rejected})
        # a non-resolved outcome never sets last_verified_at (search alone is not verification)
        return {"canonical_artist_id": canonical_artist_id, "status": status, "reason": reason,
                "method": "deterministic-evidence-v1", "identity_id": identity.id,
                "provider_id": identity.provider_id, "verified": False,
                "rejected_candidates": rejected, "candidates": cand_meta["candidates"]}

    async def resolve_youtube_from_hint(self, db: Session, canonical_artist_id: str, *,
                                        ref: Any, display_name: str | None = None) -> dict[str, Any]:
        """Resolve a YouTube identity from an operator-supplied hint (Phase 5B.1).

        A pasted channel id / @handle / video id drastically reduces ambiguity — but the hint is NEVER
        trusted on its own: whatever channel it points to is confirmed by the AUTHORITATIVE channels.list
        existence check before an identity becomes RESOLVED (parsing a URL is not verification). Handles
        and video ids are first mapped to a channel id via signal-service (forHandle / videos.list), then
        that channel id is verified. Never creates a canonical artist; ``last_verified_at`` is set only on
        a successful verification. Returns {status, reason, provider_id?, verified, lookup}."""
        from artist_intelligence_service.yturl import CHANNEL_ID, HANDLE, VIDEO_ID
        now = _now()
        meter = QuotaMeter()
        self.youtube.meter = meter
        channel_id: str | None = None
        lookup: dict[str, Any] = {"kind": ref.kind, "reference": ref.value, "raw": ref.raw}
        try:
            if ref.kind == CHANNEL_ID:
                channel_id = ref.value
            elif ref.kind == HANDLE:
                res = await self.youtube.signal.youtube_resolve_handle(ref.value)
                lookup["handle_lookup"] = res.get("status")
                channel_id = res.get("channel_id") if res.get("status") == "FOUND" else None
            elif ref.kind == VIDEO_ID:
                res = await self.youtube.signal.youtube_resolve_video(ref.value)
                lookup["video_lookup"] = res.get("status")
                channel_id = res.get("channel_id") if res.get("status") == "FOUND" else None

            if not channel_id:
                return {"canonical_artist_id": canonical_artist_id, "status": UNRESOLVED,
                        "reason": "hint_not_found_at_provider", "verified": False, "lookup": lookup}
            try:
                verification = await self.youtube.verify_channel(channel_id)
            except Exception:  # noqa: BLE001 — transient provider failure: cannot verify, do not resolve
                return {"canonical_artist_id": canonical_artist_id, "status": AMBIGUOUS,
                        "reason": "verification_unavailable", "verified": False, "lookup": lookup}
        finally:
            record_meter(db, PROVIDER_YOUTUBE, meter)

        if verification.get("status") != "FOUND":
            return {"canonical_artist_id": canonical_artist_id, "status": UNRESOLVED,
                    "reason": "channel_not_found", "provider_id": channel_id, "verified": False,
                    "lookup": lookup}

        provenance = {"resolution_method": "operator-hint+channels.list-verified",
                      "hint_kind": ref.kind, "hint": ref.raw, "verified_at": now.isoformat(),
                      "verified_provider_id": channel_id, "verified_title": verification.get("title"),
                      "verification_method": "channels.list"}
        identity = self._upsert_identity(
            db, canonical_artist_id=canonical_artist_id, provider=PROVIDER_YOUTUBE,
            identity_type="CHANNEL_ID", provider_id=channel_id, status=RESOLVED,
            display_name=verification.get("title") or display_name,
            canonical_url=f"https://www.youtube.com/channel/{channel_id}",
            resolution_method="operator-hint+channels.list-verified", confidence=1.0,
            metadata={"reason": "operator_hint_verified", "lookup": lookup},
            provenance=provenance, last_verified_at=now)
        pending = self.get_identity(db, PROVIDER_YOUTUBE, "CHANNEL_ID",
                                    idlib.pending_identity_id(canonical_artist_id))
        if pending is not None and pending.id != identity.id:
            pending.status = "REJECTED"
            pending.updated_at = now
            db.flush()
        return {"canonical_artist_id": canonical_artist_id, "status": RESOLVED,
                "reason": "operator_hint_verified", "provider_id": channel_id,
                "identity_id": identity.id, "verified": True, "lookup": lookup}

    # ---- observation ingest ------------------------------------------------------------------
    def _ingest(self, db: Session, *, canonical_artist_id: str, provider: str,
                external_identity_id: str | None, data: list[DemandDatum], observed_at: datetime,
                bucket: str) -> dict[str, int]:
        """Idempotent append: one logical observation per observation_key. Returns counts."""
        created = 0
        for d in data:
            key = idlib.observation_key(
                canonical_artist_id=canonical_artist_id, provider=provider, metric=d.metric,
                scope_type=d.scope_type, scope_id=d.scope_id, dedup_extra=d.dedup_extra, bucket=bucket)
            exists = db.execute(
                select(ArtistDemandObservation.id)
                .where(ArtistDemandObservation.observation_key == key)
            ).scalar_one_or_none()
            if exists is not None:
                continue
            db.add(ArtistDemandObservation(
                id=idlib.new_id(key), canonical_artist_id=canonical_artist_id, provider=provider,
                external_identity_id=external_identity_id, metric=d.metric,
                value_numeric=d.value_numeric, value_text=d.value_text, unit=d.unit,
                scope_type=d.scope_type, scope_id=d.scope_id, scope_label=d.scope_label,
                provider_timestamp=d.provider_timestamp, observed_at=observed_at,
                evidence_status=d.evidence_status, provenance=d.provenance,
                observation_key=key, created_at=_now()))
            created += 1
        db.flush()
        return {"received": len(data), "created": created, "duplicates": len(data) - created}

    async def snapshot_youtube(self, db: Session, canonical_artist_id: str, *,
                               channel_id: str | None = None, include_channel: bool = True,
                               include_videos: bool = True,
                               observed_at: datetime | None = None) -> dict[str, Any]:
        """Refresh a RESOLVED artist's YouTube channel (+ recent videos) into the ledger. Known-id reads
        only — never search. Idempotent per day (re-running the same day creates no duplicates)."""
        observed_at = observed_at or _now()
        identity = self._resolved_youtube_identity(db, canonical_artist_id, channel_id)
        if identity is None:
            return {"status": "NO_RESOLVED_IDENTITY", "canonical_artist_id": canonical_artist_id}
        cid = identity.provider_id
        bucket = yt_observation_bucket(observed_at)

        meter = QuotaMeter()
        self.youtube.meter = meter
        # Authoritative existence check BEFORE writing anything — a channel that has disappeared must
        # never silently produce observations or fabricated zeros. A transient/network failure raises
        # and is handled below WITHOUT invalidating the identity.
        try:
            verification = await self.youtube.verify_channel(cid)
        except Exception as exc:  # noqa: BLE001 — transient: do not invalidate; surface as retryable
            record_meter(db, PROVIDER_YOUTUBE, meter)
            return {"status": "VERIFICATION_UNAVAILABLE", "canonical_artist_id": canonical_artist_id,
                    "channel_id": cid, "error": f"{type(exc).__name__}: {exc}"}
        if verification.get("status") != "FOUND":
            record_meter(db, PROVIDER_YOUTUBE, meter)
            self._invalidate_youtube_identity(db, identity, reason="PROVIDER_ID_NOT_FOUND",
                                              at=observed_at)
            return {"status": "PROVIDER_ID_NOT_FOUND", "canonical_artist_id": canonical_artist_id,
                    "channel_id": cid}

        try:
            channel_data = self.youtube.channel_demand_from_verification(cid, verification) \
                if include_channel else []
            video_data = await self.youtube.get_content_snapshot(
                cid, limit=settings.youtube_recent_video_limit) if include_videos else []
        finally:
            record_meter(db, PROVIDER_YOUTUBE, meter)

        chan = self._ingest(db, canonical_artist_id=canonical_artist_id, provider=PROVIDER_YOUTUBE,
                            external_identity_id=identity.id, data=channel_data,
                            observed_at=observed_at, bucket=bucket)
        vids = self._ingest(db, canonical_artist_id=canonical_artist_id, provider=PROVIDER_YOUTUBE,
                           external_identity_id=identity.id, data=video_data,
                           observed_at=observed_at, bucket=bucket)
        # channel confirmed present via channels.list → this IS a real provider verification
        identity.last_verified_at = observed_at
        identity.updated_at = _now()
        db.flush()
        return {"status": "OK", "canonical_artist_id": canonical_artist_id, "channel_id": cid,
                "channel_observations": chan, "video_observations": vids}

    def _invalidate_youtube_identity(self, db: Session, identity: ArtistExternalIdentity, *,
                                     reason: str, at: datetime) -> None:
        """Authoritative NOT_FOUND → mark the identity UNRESOLVED with a reason, so it leaves normal
        recurring refresh (the scheduler only enqueues RESOLVED identities) until it is re-resolved.
        Reuses existing status semantics — no new enum. Does NOT touch last_verified_at (no successful
        verification occurred) and writes no observations."""
        identity.status = UNRESOLVED
        identity.resolution_method = "provider_verification"
        identity.identity_metadata = {**(identity.identity_metadata or {}),
                                      "invalidation_reason": reason,
                                      "invalidated_at": at.isoformat()}
        identity.updated_at = _now()
        db.flush()

    def _resolved_youtube_identity(self, db: Session, canonical_artist_id: str,
                                   channel_id: str | None) -> ArtistExternalIdentity | None:
        if channel_id:
            return self.get_identity(db, PROVIDER_YOUTUBE, "CHANNEL_ID", channel_id)
        for row in self.list_identities(db, canonical_artist_id):
            if row.provider == PROVIDER_YOUTUBE and row.identity_type == "CHANNEL_ID" \
                    and row.status == RESOLVED:
                return row
        return None

    # ---- Phase 5A.3: identity discovery (quota-guarded), catalogue backfill, registry video snapshot ----
    async def discover_identity_for_artist(self, db: Session, canonical_artist_id: str, *,
                                           display_name: str, hints: dict | None = None,
                                           purpose: str = "unresolved",
                                           others_have_backlog: bool = True,
                                           near_reset: bool = False,
                                           high_priority: bool = False) -> dict[str, Any]:
        """Quota-guarded wrapper over resolve_youtube for the identity-resolution queue. Honors the
        dynamic per-purpose SEARCH allocation + search reserve; QUOTA_EXHAUSTED → defer, never invalidate.
        ``high_priority`` (new Watchlist / operator / evidence-triggered) may use the reserved capacity."""
        from artist_intelligence_service.quota import can_spend_search
        if not can_spend_search(db, PROVIDER_YOUTUBE, purpose, YT_SEARCH_UNITS,
                                others_have_backlog=others_have_backlog, near_reset=near_reset,
                                high_priority=high_priority):
            return {"status": "QUOTA_EXHAUSTED", "canonical_artist_id": canonical_artist_id}
        return await self.resolve_youtube(db, canonical_artist_id, query=display_name, hints=hints or {},
                                          search_purpose=purpose)

    async def backfill_catalogue(self, db: Session, canonical_artist_id: str, *,
                                 depth: int | None = None,
                                 observed_at: datetime | None = None) -> dict[str, Any]:
        """One-time bounded catalogue backfill into the video REGISTRY (metadata only; observations are a
        separate job). Idempotent on video_id. Verifies the channel first (5A.1a) — a dead channel never
        fabricates a registry."""
        from artist_intelligence_service import videos as vids
        observed_at = observed_at or _now()
        depth = depth or settings.youtube_catalogue_backfill_depth
        identity = self._resolved_youtube_identity(db, canonical_artist_id, None)
        if identity is None:
            return {"status": "NO_RESOLVED_IDENTITY", "canonical_artist_id": canonical_artist_id}
        cid = identity.provider_id
        meter = QuotaMeter()
        self.youtube.meter = meter
        try:
            verification = await self.youtube.verify_channel(cid)
        except Exception as exc:  # noqa: BLE001 — transient: defer, never invalidate
            record_meter(db, PROVIDER_YOUTUBE, meter)
            return {"status": "VERIFICATION_UNAVAILABLE", "canonical_artist_id": canonical_artist_id,
                    "channel_id": cid, "error": f"{type(exc).__name__}: {exc}"}
        if verification.get("status") != "FOUND":
            record_meter(db, PROVIDER_YOUTUBE, meter)
            self._invalidate_youtube_identity(db, identity, reason="PROVIDER_ID_NOT_FOUND", at=observed_at)
            return {"status": "PROVIDER_ID_NOT_FOUND", "canonical_artist_id": canonical_artist_id,
                    "channel_id": cid}
        try:
            uploads = await self.youtube.list_uploads(cid, limit=depth)
        finally:
            record_meter(db, PROVIDER_YOUTUBE, meter)
        created = 0
        for v in uploads.get("videos") or []:
            vid = v.get("video_id")
            if not vid:
                continue
            _, is_new = vids.upsert_video(
                db, video_id=vid, channel_id=cid, canonical_artist_id=canonical_artist_id,
                external_identity_id=identity.id, title=v.get("title"),
                published_at=ytprov._parse_ts(v.get("published_at")),
                duration_seconds=None, category=None, live_status=None,
                # 5B.2.7 §14 — explicit: these are the artist's OWN uploads via the official uploads playlist
                relationship_type="OWNED_CONTENT", discovery_method="CHANNEL_UPLOADS",
                metadata={"source": "uploads_playlist"}, now=observed_at)
            created += 1 if is_new else 0
        return {"status": "OK", "canonical_artist_id": canonical_artist_id, "channel_id": cid,
                "videos_seen": len(uploads.get("videos") or []), "videos_registered": created}

    async def snapshot_videos_registry(self, db: Session, canonical_artist_id: str, *,
                                       observed_at: datetime | None = None,
                                       use_batch: bool = True) -> dict[str, Any]:
        """Refresh video demand observations for the artist's REGISTERED videos using the quota-efficient
        batch primitive (falls back to the recent-videos path if the registry is empty). Hourly buckets."""
        from artist_intelligence_service import videos as vids
        observed_at = observed_at or _now()
        identity = self._resolved_youtube_identity(db, canonical_artist_id, None)
        if identity is None:
            return {"status": "NO_RESOLVED_IDENTITY", "canonical_artist_id": canonical_artist_id}
        bucket = yt_observation_bucket(observed_at)
        registered = vids.videos_for_artist(db, canonical_artist_id)
        meter = QuotaMeter()
        self.youtube.meter = meter
        try:
            if registered and use_batch:
                ids = [v.video_id for v in registered][:settings.youtube_catalogue_backfill_depth]
                # batch in chunks of 50 (the videos.list id limit)
                data = []
                for i in range(0, len(ids), 50):
                    data.extend(await self.youtube.get_video_stats_batch(ids[i:i + 50]))
                path = "batch"
            else:
                data = await self.youtube.get_content_snapshot(
                    identity.provider_id, limit=settings.youtube_recent_video_limit)
                path = "recent_fallback"
        finally:
            record_meter(db, PROVIDER_YOUTUBE, meter)
        counts = self._ingest(db, canonical_artist_id=canonical_artist_id, provider=PROVIDER_YOUTUBE,
                              external_identity_id=identity.id, data=data, observed_at=observed_at,
                              bucket=bucket)
        return {"status": "OK", "canonical_artist_id": canonical_artist_id, "path": path,
                "video_observations": counts}

    # ---- Trends import -----------------------------------------------------------------------
    def import_trends(self, db: Session, canonical_artist_id: str, *, csv_text: str,
                      identity_type: str, provider_id: str, geo: str, time_range: str | None = None,
                      comparison_window: str | None = None, source_file: str | None = None,
                      export_timestamp: str | None = None,
                      observed_at: datetime | None = None) -> dict[str, Any]:
        """Ingest one legitimately-obtained Trends CSV export. Idempotent per export fingerprint.

        identity_type is SEARCH_TERM or TOPIC_ID — persisted so search-term and topic histories are
        never silently combined."""
        if identity_type not in ("SEARCH_TERM", "TOPIC_ID"):
            raise ValueError("identity_type must be SEARCH_TERM or TOPIC_ID")
        observed_at = observed_at or _now()
        parsed = parse_trends_csv(csv_text)   # raises TrendsImportError on malformed input
        data = self.trends_import.build_data(
            parsed, identity_type=identity_type, provider_id=provider_id, geo=geo,
            time_range=time_range, comparison_window=comparison_window, source_file=source_file,
            export_timestamp=export_timestamp)

        identity = self._upsert_identity(
            db, canonical_artist_id=canonical_artist_id, provider=PROVIDER_GOOGLE_TRENDS,
            identity_type=identity_type, provider_id=provider_id, status=RESOLVED,
            display_name=parsed.label, canonical_url=None, resolution_method="operator_import",
            confidence=1.0, metadata={"kind": parsed.kind, "aggregation": parsed.aggregation})

        fingerprint = idlib.export_fingerprint(
            label=parsed.label, geo=geo, time_range=time_range,
            comparison_window=comparison_window, source_file=source_file)
        counts = self._ingest(db, canonical_artist_id=canonical_artist_id,
                              provider=PROVIDER_GOOGLE_TRENDS, external_identity_id=identity.id,
                              data=data, observed_at=observed_at, bucket=fingerprint)
        return {"status": "OK", "canonical_artist_id": canonical_artist_id, "kind": parsed.kind,
                "identity_type": identity_type, "provider_id": provider_id, "geo": geo,
                "export_fingerprint": fingerprint, "observations": counts}

    def trends_official_status(self) -> dict[str, Any]:
        if self.trends_official.available:
            return {"mode": "OFFICIAL_API", "status": "AVAILABLE"}
        return {"mode": settings.resolved_trends_mode, "status": "ACCESS_UNAVAILABLE",
                "reason": "Google Trends API alpha credentials/endpoint not configured",
                "fallback": "IMPORT"}
