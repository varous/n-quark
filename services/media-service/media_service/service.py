"""MediaObservationService — orchestrates one creative observation end to end (Phase 4B).

normalize URL → (optionally) safe-fetch bytes → content identity (SHA-256) + header metadata →
upsert content-addressed asset → record observation (idempotent) → deterministic transition detection
vs current per-(event, source, role) state → append media transitions → best-effort graph link.

Every step is deterministic and non-destructive; a fetch failure preserves the last valid state and a
failed graph link never fails the observation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from media_service import fetcher as fetchmod
from media_service import identity, metadata, transitions
from media_service.config import Settings, settings
from media_service.models import EventMediaState, MediaAsset, MediaObservation, MediaTransition
from media_service.storage import ContentAddressedStore

ASSET_ROLES = ("POSTER", "THUMBNAIL", "HERO_IMAGE", "LISTING_IMAGE", "UNKNOWN")
URL_ONLY = "URL_ONLY"


@dataclass
class ObserveInput:
    canonical_event_id: str
    source: str
    asset_url: str | None = None
    asset_role: str = "UNKNOWN"
    source_record_id: str | None = None
    observed_at: datetime | None = None
    source_page_url: str | None = None
    trace_id: str | None = None
    authoritative: bool = False   # reflects a successful source capture (required for disappearance)


@dataclass
class MediaService:
    session_factory: Callable
    fetch_fn: Callable | None = None
    store: ContentAddressedStore | None = None
    graph: Any | None = None
    cfg: Settings = field(default_factory=lambda: settings)

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _role(self, role: str | None) -> str:
        r = (role or "UNKNOWN").upper()
        return r if r in ASSET_ROLES else "UNKNOWN"

    async def observe(self, inp: ObserveInput) -> dict[str, Any]:
        role = self._role(inp.asset_role)
        observed_at = inp.observed_at or self._now()
        normalized = identity.normalize_url(inp.asset_url) if inp.asset_url else ""

        # --- absence (authoritative) vs presence ---
        absent = not inp.asset_url and inp.authoritative
        media_asset_id: str | None = None
        fetch_status = URL_ONLY
        http_status = content_type = error_class = None

        if not absent and inp.asset_url:
            if self.cfg.media_fetch_enabled and self.fetch_fn is not None:
                res = await self.fetch_fn(inp.asset_url)
                fetch_status, http_status, content_type = res.status, res.http_status, res.content_type
                if res.ok and res.data is not None:
                    media_asset_id = self._upsert_asset(res.data, res.content_type, observed_at)
                else:
                    error_class = res.status  # a failed fetch: preserve state, no transition
            else:
                fetch_status = URL_ONLY

        with self.session_factory() as s, s.begin():
            # idempotent observation on (event, source, role, normalized_url, observed_at)
            existing = s.execute(
                select(MediaObservation).where(
                    MediaObservation.canonical_event_id == inp.canonical_event_id,
                    MediaObservation.source == inp.source,
                    MediaObservation.asset_role == role,
                    MediaObservation.normalized_url == normalized,
                    MediaObservation.observed_at == observed_at,
                )
            ).scalar_one_or_none()
            if existing is not None:
                state = self._state_dict(s, inp.canonical_event_id, inp.source, role)
                return {"observation_id": existing.id, "media_asset_id": existing.media_asset_id,
                        "fetch_status": existing.fetch_status, "transitions": [], "idempotent": True,
                        "state": state}

            obs = MediaObservation(
                canonical_event_id=inp.canonical_event_id, source=inp.source,
                source_record_id=inp.source_record_id, asset_role=role,
                asset_url=inp.asset_url or "", normalized_url=normalized,
                media_asset_id=media_asset_id, observed_at=observed_at,
                fetch_status="ABSENT" if absent else fetch_status,
                http_status=http_status, content_type=content_type,
                error_class=error_class, trace_id=inp.trace_id)
            s.add(obs)
            s.flush()

            # transition detection runs only on a definite signal (present w/ identity, or authoritative
            # absence). A failed fetch records the observation but changes no state.
            fetch_failed = (not absent and inp.asset_url and media_asset_id is None
                            and fetch_status not in (URL_ONLY,))
            applied: list[str] = []
            out_of_order = False
            if absent:
                applied, out_of_order = self._apply(s, inp, role, transitions.ObsFacts(
                    kind="ABSENT", media_asset_id=None, normalized_url=normalized,
                    observed_at=observed_at, authoritative_absence=True), obs.id)
            elif not fetch_failed and inp.asset_url:
                applied, out_of_order = self._apply(s, inp, role, transitions.ObsFacts(
                    kind="PRESENT", media_asset_id=media_asset_id, normalized_url=normalized,
                    observed_at=observed_at), obs.id)

            s.flush()  # make the just-applied state/transition visible (session is autoflush=False)
            state = self._state_dict(s, inp.canonical_event_id, inp.source, role)
            result = {"observation_id": obs.id, "media_asset_id": media_asset_id,
                      "fetch_status": obs.fetch_status, "transitions": applied,
                      "out_of_order": out_of_order, "idempotent": False, "state": state}

        # best-effort graph link (outside the txn; never fails the observation)
        if media_asset_id and self.cfg.media_graph_link_enabled and self.graph is not None:
            await self._link_graph(inp, role, media_asset_id, observed_at, result["state"])
        return result

    def _upsert_asset(self, data: bytes, content_type: str | None, now: datetime) -> str:
        sha = identity.content_sha256(data)
        meta = metadata.extract(data)
        storage_key = self.store.write(sha, data) if self.store else None
        with self.session_factory() as s, s.begin():
            asset = s.execute(select(MediaAsset).where(MediaAsset.content_sha256 == sha)).scalar_one_or_none()
            if asset is None:
                asset = MediaAsset(
                    content_sha256=sha, mime_type=(meta.mime_type if meta else content_type),
                    byte_size=len(data), width=(meta.width if meta else None),
                    height=(meta.height if meta else None),
                    image_format=(meta.image_format if meta else None),
                    storage_key=storage_key, fetch_status=fetchmod.FETCHED,
                    first_seen_at=now, last_seen_at=now)
                s.add(asset)
                s.flush()
            else:
                asset.last_seen_at = now
                if storage_key and not asset.storage_key:
                    asset.storage_key = storage_key
            return asset.id

    def _apply(self, s, inp: ObserveInput, role: str, facts: transitions.ObsFacts,
               observation_id: str) -> tuple[list[str], bool]:
        row = s.execute(
            select(EventMediaState).where(
                EventMediaState.canonical_event_id == inp.canonical_event_id,
                EventMediaState.source == inp.source, EventMediaState.asset_role == role)
        ).scalar_one_or_none()
        prev = None if row is None else transitions.StateView(
            present=row.present, media_asset_id=row.current_media_asset_id,
            normalized_url=row.current_normalized_url, first_seen_at=_aware(row.first_seen_at),
            last_observed_at=_aware(row.last_observed_at), last_changed_at=_aware(row.last_changed_at),
            version=row.version)
        decision = transitions.detect(prev, facts)

        from_asset = prev.media_asset_id if prev else None
        from_url = prev.normalized_url if prev else None
        for ttype in decision.transitions:
            s.add(MediaTransition(
                canonical_event_id=inp.canonical_event_id, source=inp.source, asset_role=role,
                transition_type=ttype, from_media_asset_id=from_asset,
                to_media_asset_id=facts.media_asset_id, from_normalized_url=from_url,
                to_normalized_url=facts.normalized_url, observation_id=observation_id,
                out_of_order=decision.out_of_order, detected_at=facts.observed_at))

        ns = decision.new_state
        if ns is not None:
            if row is None:
                s.add(EventMediaState(
                    canonical_event_id=inp.canonical_event_id, source=inp.source, asset_role=role,
                    current_media_asset_id=ns.media_asset_id, current_observation_id=observation_id,
                    current_normalized_url=ns.normalized_url, present=ns.present,
                    first_seen_at=ns.first_seen_at, last_observed_at=ns.last_observed_at,
                    last_changed_at=ns.last_changed_at, version=ns.version))
            else:
                row.current_media_asset_id = ns.media_asset_id
                row.current_observation_id = observation_id
                row.current_normalized_url = ns.normalized_url
                row.present = ns.present
                row.last_observed_at = ns.last_observed_at
                row.last_changed_at = ns.last_changed_at
                row.version = ns.version
        return decision.transitions, decision.out_of_order

    def _state_dict(self, s, event_id: str, source: str, role: str) -> dict[str, Any] | None:
        row = s.execute(
            select(EventMediaState).where(
                EventMediaState.canonical_event_id == event_id,
                EventMediaState.source == source, EventMediaState.asset_role == role)
        ).scalar_one_or_none()
        if row is None:
            return None
        return {"present": row.present, "version": row.version,
                "current_media_asset_id": row.current_media_asset_id,
                "current_normalized_url": row.current_normalized_url,
                "first_seen_at": _iso(row.first_seen_at), "last_changed_at": _iso(row.last_changed_at)}

    async def _link_graph(self, inp: ObserveInput, role: str, asset_id: str, now: datetime,
                          state: dict | None) -> None:
        try:
            with self.session_factory() as s:
                asset = s.get(MediaAsset, asset_id)
                props = {} if asset is None else {
                    "content_sha256": asset.content_sha256, "mime_type": asset.mime_type,
                    "width": asset.width, "height": asset.height, "image_format": asset.image_format}
            await self.graph.upsert_media_asset(asset_id, props)
            await self.graph.link_uses_creative(inp.canonical_event_id, asset_id, {
                "source": inp.source, "asset_role": role,
                "last_observed": _iso(now), "present": (state or {}).get("present", True),
                "version": (state or {}).get("version")})
        except Exception:  # noqa: BLE001 — a graph-link failure must never fail the observation
            return


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _aware(dt: datetime | None) -> datetime | None:
    """Coerce a stored datetime to UTC-aware (SQLite returns naive; Postgres returns aware)."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
