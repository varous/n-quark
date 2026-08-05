"""Read models over the media tables (Phase 4B) — assets, event creatives, timelines, coverage,
failures. Bounded + paginated. This is *observed* creative coverage, never total creative-market
coverage."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from media_service import fetcher as fetchmod
from media_service import transitions as T
from media_service.models import EventMediaState, MediaAsset, MediaObservation, MediaTransition

FAILURE_STATUSES = (fetchmod.NOT_FOUND, fetchmod.SOURCE_UNAVAILABLE, fetchmod.BLOCKED,
                    fetchmod.INVALID_CONTENT, fetchmod.TOO_LARGE, fetchmod.TIMEOUT,
                    fetchmod.UNSUPPORTED_TYPE)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class MediaReads:
    def __init__(self, session_factory: Callable) -> None:
        self._sf = session_factory

    # ---- assets --------------------------------------------------------------------------------
    def list_assets(self, *, source: str | None = None, fetch_status: str | None = None,
                    limit: int = 50, offset: int = 0) -> dict[str, Any]:
        with self._sf() as s:
            stmt = select(MediaAsset)
            if fetch_status:
                stmt = stmt.where(MediaAsset.fetch_status == fetch_status)
            if source:
                sub = select(MediaObservation.media_asset_id).where(
                    MediaObservation.source == source, MediaObservation.media_asset_id.isnot(None))
                stmt = stmt.where(MediaAsset.id.in_(sub))
            total = s.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = s.execute(stmt.order_by(MediaAsset.first_seen_at.desc())
                             .offset(offset).limit(limit)).scalars().all()
            return {"count": total, "limit": limit, "offset": offset,
                    "items": [self._asset_dict(a) for a in rows]}

    def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        with self._sf() as s:
            asset = s.get(MediaAsset, asset_id)
            if asset is None:
                return None
            obs = s.execute(select(MediaObservation).where(
                MediaObservation.media_asset_id == asset_id)).scalars().all()
            events = sorted({o.canonical_event_id for o in obs})
            urls = sorted({o.normalized_url for o in obs})
            return {**self._asset_dict(asset), "observation_count": len(obs),
                    "linked_events": events, "distinct_urls": urls}

    @staticmethod
    def _asset_dict(a: MediaAsset) -> dict[str, Any]:
        ar = round(a.width / a.height, 4) if a.width and a.height else None
        return {"id": a.id, "content_sha256": a.content_sha256, "perceptual_hash": a.perceptual_hash,
                "mime_type": a.mime_type, "byte_size": a.byte_size, "width": a.width,
                "height": a.height, "image_format": a.image_format, "aspect_ratio": ar,
                "storage_key": a.storage_key, "fetch_status": a.fetch_status,
                "first_seen_at": _iso(a.first_seen_at), "last_seen_at": _iso(a.last_seen_at)}

    # ---- per-event -----------------------------------------------------------------------------
    def event_assets(self, event_id: str) -> dict[str, Any]:
        with self._sf() as s:
            states = s.execute(select(EventMediaState).where(
                EventMediaState.canonical_event_id == event_id)).scalars().all()
            out = []
            for st in states:
                asset = s.get(MediaAsset, st.current_media_asset_id) if st.current_media_asset_id else None
                out.append({
                    "source": st.source, "asset_role": st.asset_role, "present": st.present,
                    "version": st.version, "current_normalized_url": st.current_normalized_url,
                    "first_seen_at": _iso(st.first_seen_at), "last_changed_at": _iso(st.last_changed_at),
                    "asset": self._asset_dict(asset) if asset else None})
            return {"canonical_event_id": event_id, "creatives": out}

    def event_timeline(self, event_id: str, *, source: str | None = None, asset_role: str | None = None,
                       changed_only: bool = False, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        with self._sf() as s:
            stmt = select(MediaTransition).where(MediaTransition.canonical_event_id == event_id)
            if source:
                stmt = stmt.where(MediaTransition.source == source)
            if asset_role:
                stmt = stmt.where(MediaTransition.asset_role == asset_role)
            if changed_only:
                stmt = stmt.where(MediaTransition.transition_type != T.MEDIA_FIRST_SEEN)
            total = s.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = s.execute(stmt.order_by(MediaTransition.detected_at.asc())
                             .offset(offset).limit(limit)).scalars().all()
            return {"canonical_event_id": event_id, "count": total, "limit": limit, "offset": offset,
                    "transitions": [{
                        "transition_type": t.transition_type, "source": t.source,
                        "asset_role": t.asset_role, "from_media_asset_id": t.from_media_asset_id,
                        "to_media_asset_id": t.to_media_asset_id, "out_of_order": t.out_of_order,
                        "detected_at": _iso(t.detected_at)} for t in rows]}

    # ---- coverage + failures -------------------------------------------------------------------
    def coverage(self, *, source: str | None = None) -> dict[str, Any]:
        with self._sf() as s:
            obs = s.execute(select(MediaObservation)).scalars().all()
            trans = s.execute(select(MediaTransition)).scalars().all()
            states = s.execute(select(EventMediaState)).scalars().all()
        sources = sorted({o.source for o in obs}) if source is None else [source]
        by_source = {src: self._coverage_for(src, obs, trans, states) for src in sources}
        return {"by_source": by_source,
                "note": "Observed creative coverage from configured adapters — not total creative-market coverage."}

    @staticmethod
    def _coverage_for(src: str, obs: list[MediaObservation], trans: list[MediaTransition],
                      states: list[EventMediaState]) -> dict[str, Any]:
        o = [x for x in obs if x.source == src]
        t = [x for x in trans if x.source == src]
        st = [x for x in states if x.source == src]
        events_inspected = {x.canonical_event_id for x in o}
        events_with_refs = {x.canonical_event_id for x in o if x.asset_url}
        fetched = [x for x in o if x.fetch_status == fetchmod.FETCHED]
        failed = [x for x in o if x.fetch_status in FAILURE_STATUSES]
        failed_by_class: dict[str, int] = {}
        for x in failed:
            failed_by_class[x.fetch_status] = failed_by_class.get(x.fetch_status, 0) + 1
        asset_urls: dict[str, set] = {}
        for x in o:
            if x.media_asset_id:
                asset_urls.setdefault(x.media_asset_id, set()).add(x.normalized_url)
        duplicate_content_urls = sum(1 for urls in asset_urls.values() if len(urls) > 1)
        return {
            "events_inspected": len(events_inspected),
            "events_with_asset_references": len(events_with_refs),
            "asset_reference_coverage": (round(len(events_with_refs) / len(events_inspected), 3)
                                         if events_inspected else None),
            "successful_fetches": len(fetched),
            "failed_fetches_by_class": failed_by_class,
            "unique_content_assets": len({x.media_asset_id for x in o if x.media_asset_id}),
            "duplicate_content_urls": duplicate_content_urls,
            "events_with_multiple_asset_versions": sum(1 for x in st if x.version > 1),
            "content_changes": sum(1 for x in t if x.transition_type == T.MEDIA_CONTENT_CHANGED),
            "url_only_changes": sum(1 for x in t if x.transition_type == T.MEDIA_URL_CHANGED_SAME_CONTENT),
            "disappearances": sum(1 for x in t if x.transition_type == T.MEDIA_DISAPPEARED),
            "reappearances": sum(1 for x in t if x.transition_type == T.MEDIA_REAPPEARED),
        }

    def failures(self, *, source: str | None = None, error_class: str | None = None,
                 limit: int = 50, offset: int = 0) -> dict[str, Any]:
        with self._sf() as s:
            stmt = select(MediaObservation).where(MediaObservation.fetch_status.in_(FAILURE_STATUSES))
            if source:
                stmt = stmt.where(MediaObservation.source == source)
            if error_class:
                stmt = stmt.where(MediaObservation.fetch_status == error_class)
            total = s.scalar(select(func.count()).select_from(stmt.subquery()))
            rows = s.execute(stmt.order_by(MediaObservation.observed_at.desc())
                             .offset(offset).limit(limit)).scalars().all()
            return {"count": total, "limit": limit, "offset": offset,
                    "items": [{"canonical_event_id": r.canonical_event_id, "source": r.source,
                               "asset_role": r.asset_role, "asset_url": r.asset_url,
                               "fetch_status": r.fetch_status, "http_status": r.http_status,
                               "error_class": r.error_class, "observed_at": _iso(r.observed_at)}
                              for r in rows]}

    # ---- stable analytics read contract --------------------------------------------------------
    def event_creative_summary(self, event_id: str) -> dict[str, Any]:
        """Stable internal contract for analytics to consume later (not wired into Phase 4A)."""
        with self._sf() as s:
            states = s.execute(select(EventMediaState).where(
                EventMediaState.canonical_event_id == event_id)).scalars().all()
            trans = s.execute(select(MediaTransition).where(
                MediaTransition.canonical_event_id == event_id)).scalars().all()
            obs = s.execute(select(MediaObservation).where(
                MediaObservation.canonical_event_id == event_id)).scalars().all()
        first = min((o.observed_at for o in obs), default=None)
        last_changed = max((st.last_changed_at for st in states), default=None)
        return {
            "canonical_event_id": event_id,
            "creative_version_count": sum(st.version for st in states),
            "unique_creative_count": len({st.current_media_asset_id for st in states
                                          if st.current_media_asset_id}),
            "first_creative_observed": _iso(first),
            "last_creative_changed": _iso(last_changed),
            "content_changes": sum(1 for t in trans if t.transition_type == T.MEDIA_CONTENT_CHANGED),
            "sources": sorted({st.source for st in states}),
        }
