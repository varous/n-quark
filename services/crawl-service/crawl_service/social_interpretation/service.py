"""SocialInterpretationService (Phase 5C.2).

A bounded, deterministic, retry-safe loop over UNPROCESSED immutable social evidence. For each evidence
version it: classifies (pure), creates or versions a ``SocialInterpretation`` (never mutating the
evidence), decides event-bearing, and — for event-bearing evidence — projects into the EXISTING
reconciliation surface (``event_match_candidate``). It only ever mutates *workflow* metadata on the
evidence (``processing_status``); the observed evidence fields are never touched, and no canonical Event
is created.

Idempotency / versioning:
  * A ``SocialInterpretation`` is keyed to the exact evidence version (``social_mention_id``) + a
    ``classifier_version``. Re-interpreting the same evidence with the same classifier version is a
    no-op (the current interpretation already reflects it).
  * A changed classifier version INSERTS a new interpretation version (``previous_interpretation_id``
    links back; the prior row's ``is_current`` flips) — the prior interpretation is preserved.
  * A failure on one evidence row backs off that row without corrupting the evidence or other rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from crawl_service.config import Settings, settings
from crawl_service.models import EventMatchCandidate, SocialInterpretation, SocialMention
from crawl_service.reconciliation import matcher as M
from crawl_service.reconciliation.views import EventView
from crawl_service.social_interpretation import classifier as C
from crawl_service.social_interpretation import projection as P

ExistingViewsProvider = Callable[[], Awaitable[list[EventView]]]


def _uuid() -> str:
    return uuid.uuid4().hex


def _pair_key(a: str, b: str) -> str:
    return "::".join(sorted([a, b]))


async def _no_existing_views() -> list[EventView]:
    return []


class SocialInterpretationService:
    def __init__(self, session_factory, config: Settings | None = None,
                 existing_views_provider: ExistingViewsProvider | None = None) -> None:
        self._sf = session_factory
        self._cfg = config or settings
        self._existing_views = existing_views_provider or _no_existing_views

    # ---- processing loop -------------------------------------------------------------------------
    async def run_once(self, *, limit: int | None = None, now: datetime | None = None,
                       trace: bool = False) -> dict[str, Any]:
        """One bounded pass over UNPROCESSED current evidence versions. Retry-safe + idempotent."""
        now = now or datetime.now(UTC)
        cap = min(limit or self._cfg.social_interpretation_max_per_run,
                  self._cfg.social_interpretation_max_per_run)
        with self._sf() as s:
            rows = s.execute(
                select(SocialMention).where(
                    SocialMention.is_current.is_(True),
                    SocialMention.processing_status == "UNPROCESSED",
                ).order_by(SocialMention.observed_at).limit(cap)
            ).scalars().all()
            mention_ids = [m.id for m in rows]

        existing = await self._existing_views() if mention_ids else []
        metrics = {"selected": len(mention_ids), "interpreted": 0, "event_bearing": 0,
                   "candidates": 0, "errors": 0,
                   "by_status": {}, "by_primary": {}}
        traces: list[dict] = []
        for mid in mention_ids:
            try:
                outcome = self._interpret_one(mid, existing, now)
            except Exception as exc:  # noqa: BLE001 — one bad row must not corrupt the batch/evidence
                metrics["errors"] += 1
                self._mark_status(mid, "INTERPRETATION_ERROR", now)
                if trace:
                    traces.append({"mention_id": mid, "error": str(exc)})
                continue
            metrics["interpreted"] += 1
            if outcome["event_bearing"]:
                metrics["event_bearing"] += 1
            if outcome.get("candidate_written"):
                metrics["candidates"] += 1
            st = outcome["event_candidate_status"]
            metrics["by_status"][st] = metrics["by_status"].get(st, 0) + 1
            pc = outcome.get("primary_claim_type") or "UNKNOWN"
            metrics["by_primary"][pc] = metrics["by_primary"].get(pc, 0) + 1
            if trace:
                traces.append(outcome)

        result = {"metrics": metrics, "classifier_version": C.CLASSIFIER_VERSION}
        if trace:
            result["items"] = traces
        return result

    def interpret_mention(self, mention_id: str, existing: list[EventView] | None = None,
                          now: datetime | None = None) -> dict[str, Any]:
        """Interpret one evidence version synchronously (no projection provider). Idempotent."""
        return self._interpret_one(mention_id, existing or [], now or datetime.now(UTC))

    def _interpret_one(self, mention_id: str, existing: list[EventView],
                       now: datetime) -> dict[str, Any]:
        with self._sf() as s, s.begin():
            mention = s.get(SocialMention, mention_id)
            if mention is None:
                raise ValueError(f"unknown social_mention {mention_id}")

            interp = C.classify(dict(mention.extracted_claims or {}))

            current = s.execute(
                select(SocialInterpretation).where(
                    SocialInterpretation.social_mention_id == mention_id,
                    SocialInterpretation.is_current.is_(True))
            ).scalar_one_or_none()

            # idempotent: same evidence version already interpreted by this classifier version
            if current is not None and current.classifier_version == C.CLASSIFIER_VERSION:
                if mention.processing_status == "UNPROCESSED":
                    mention.processing_status = "INTERPRETED"
                    mention.updated_at = now
                return self._outcome(mention, current, candidate_written=False, idempotent=True)

            # project event-bearing evidence into the EXISTING reconciliation surface
            status = P.NONE
            matched_event_id: str | None = None
            match_score: float | None = None
            candidate_id: str | None = None
            candidate_written = False
            if interp.event_bearing:
                sv = P.social_event_view(social_mention_id=mention_id,
                                         interpreted_fields=interp.interpreted_fields)
                proj = P.project(
                    sv, existing,
                    date_tolerance_hours=self._cfg.reconciliation_date_tolerance_hours,
                    auto_threshold=self._cfg.reconciliation_auto_match_threshold,
                    possible_threshold=self._cfg.reconciliation_possible_match_threshold)
                status = proj.status
                if proj.match is not None and proj.matched_view is not None:
                    match_score = proj.match.score
                    matched_event_id = proj.matched_view.canonical_event_id
                    if self._cfg.social_interpretation_project_candidates:
                        candidate_id = self._persist_candidate(s, sv, proj, now)
                        candidate_written = True

            new_version = (current.version + 1) if current else 1
            if current is not None:
                current.is_current = False
                current.superseded_at = now
                current.updated_at = now

            row = SocialInterpretation(
                id=_uuid(), social_mention_id=mention_id, evidence_version=mention.version,
                platform=mention.platform, platform_post_id=mention.platform_post_id,
                content_hash=mention.content_hash, canonical_entity_id=mention.canonical_entity_id,
                classifier_version=C.CLASSIFIER_VERSION,
                claim_types=interp.claim_types, primary_claim_type=interp.primary_claim_type,
                interpreted_fields=interp.interpreted_fields,
                supporting_evidence=interp.supporting_evidence,
                contradicting_evidence=interp.contradicting_evidence,
                confidence=interp.confidence, reason_codes=interp.reason_codes,
                event_bearing=interp.event_bearing, event_candidate_status=status,
                matched_canonical_event_id=matched_event_id, match_score=match_score,
                event_match_candidate_id=candidate_id, interpretation_status="INTERPRETED",
                version=new_version,
                previous_interpretation_id=current.id if current else None,
                created_at=now, updated_at=now)
            s.add(row)
            mention.processing_status = "INTERPRETED"
            mention.updated_at = now
            return self._outcome(mention, row, candidate_written=candidate_written, idempotent=False)

    def _persist_candidate(self, s, sv: EventView, proj: P.ProjectionResult, now) -> str:
        rv = proj.matched_view
        res = proj.match
        pk = _pair_key(f"{sv.source}:{sv.source_record_id}", f"{rv.source}:{rv.source_record_id}")
        existing = s.execute(
            select(EventMatchCandidate).where(EventMatchCandidate.pair_key == pk)
        ).scalar_one_or_none()
        review = "AUTO" if res.status == M.AUTO_MATCH else "NEEDS_REVIEW"
        if existing is None:
            cid = _uuid()
            s.add(EventMatchCandidate(
                id=cid, pair_key=pk,
                left_source=sv.source, left_source_record_id=sv.source_record_id,
                left_canonical_event_id=None,
                right_source=rv.source, right_source_record_id=rv.source_record_id,
                right_canonical_event_id=rv.canonical_event_id,
                match_status=res.status, match_score=res.score, component_scores=res.components,
                supporting_signals=res.supporting, contradicting_signals=res.contradicting,
                reason_code=res.reason_code, matcher_version=M.MATCHER_VERSION,
                review_status=review, created_at=now, updated_at=now))
            return cid
        existing.match_status = res.status
        existing.match_score = res.score
        existing.component_scores = res.components
        existing.supporting_signals = res.supporting
        existing.contradicting_signals = res.contradicting
        existing.reason_code = res.reason_code
        existing.review_status = review
        existing.updated_at = now
        return existing.id

    def _mark_status(self, mention_id: str, status: str, now: datetime) -> None:
        with self._sf() as s, s.begin():
            m = s.get(SocialMention, mention_id)
            if m is not None:
                m.processing_status = status
                m.updated_at = now

    @staticmethod
    def _outcome(mention: SocialMention, row: SocialInterpretation, *, candidate_written: bool,
                 idempotent: bool) -> dict[str, Any]:
        return {
            "mention_id": mention.id, "evidence_version": mention.version,
            "interpretation_id": row.id, "interpretation_version": row.version,
            "classifier_version": row.classifier_version,
            "claim_types": list(row.claim_types), "primary_claim_type": row.primary_claim_type,
            "event_bearing": row.event_bearing, "event_candidate_status": row.event_candidate_status,
            "matched_canonical_event_id": row.matched_canonical_event_id,
            "confidence": row.confidence, "reason_codes": list(row.reason_codes),
            "candidate_written": candidate_written, "idempotent": idempotent,
        }

    # ---- reads -----------------------------------------------------------------------------------
    def _serialize(self, r: SocialInterpretation) -> dict[str, Any]:
        return {
            "id": r.id, "social_mention_id": r.social_mention_id,
            "evidence_version": r.evidence_version, "platform": r.platform,
            "platform_post_id": r.platform_post_id, "canonical_entity_id": r.canonical_entity_id,
            "classifier_version": r.classifier_version, "claim_types": list(r.claim_types),
            "primary_claim_type": r.primary_claim_type, "interpreted_fields": dict(r.interpreted_fields),
            "supporting_evidence": list(r.supporting_evidence),
            "contradicting_evidence": list(r.contradicting_evidence),
            "confidence": r.confidence, "reason_codes": list(r.reason_codes),
            "event_bearing": r.event_bearing, "event_candidate_status": r.event_candidate_status,
            "matched_canonical_event_id": r.matched_canonical_event_id, "match_score": r.match_score,
            "event_match_candidate_id": r.event_match_candidate_id,
            "interpretation_status": r.interpretation_status,
            "version": r.version, "is_current": r.is_current,
            "previous_interpretation_id": r.previous_interpretation_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }

    def interpretations(self, *, event_bearing: bool | None = None,
                        event_candidate_status: str | None = None,
                        canonical_entity_id: str | None = None, current_only: bool = True,
                        limit: int = 100) -> dict[str, Any]:
        with self._sf() as s:
            q = select(SocialInterpretation)
            if current_only:
                q = q.where(SocialInterpretation.is_current.is_(True))
            if event_bearing is not None:
                q = q.where(SocialInterpretation.event_bearing.is_(event_bearing))
            if event_candidate_status:
                q = q.where(SocialInterpretation.event_candidate_status == event_candidate_status)
            if canonical_entity_id:
                q = q.where(SocialInterpretation.canonical_entity_id == canonical_entity_id)
            rows = s.execute(
                q.order_by(SocialInterpretation.created_at.desc()).limit(limit)).scalars().all()
            return {"interpretations": [self._serialize(r) for r in rows], "count": len(rows)}

    def interpretation_history(self, social_mention_id: str) -> dict[str, Any]:
        """Full interpretation version lineage for one evidence version (immutable, oldest→newest)."""
        with self._sf() as s:
            rows = s.execute(
                select(SocialInterpretation).where(
                    SocialInterpretation.social_mention_id == social_mention_id
                ).order_by(SocialInterpretation.version)).scalars().all()
            return {"social_mention_id": social_mention_id,
                    "versions": [self._serialize(r) for r in rows], "count": len(rows)}

    def coverage(self) -> dict[str, Any]:
        with self._sf() as s:
            total = s.execute(select(func.count()).select_from(SocialInterpretation)).scalar() or 0
            current = s.execute(select(func.count()).select_from(SocialInterpretation).where(
                SocialInterpretation.is_current.is_(True))).scalar() or 0
            event_bearing = s.execute(select(func.count()).select_from(SocialInterpretation).where(
                SocialInterpretation.is_current.is_(True),
                SocialInterpretation.event_bearing.is_(True))).scalar() or 0
            unprocessed = s.execute(select(func.count()).select_from(SocialMention).where(
                SocialMention.is_current.is_(True),
                SocialMention.processing_status == "UNPROCESSED")).scalar() or 0
            by_status: dict[str, int] = {}
            for st, cnt in s.execute(
                select(SocialInterpretation.event_candidate_status, func.count())
                .where(SocialInterpretation.is_current.is_(True))
                .group_by(SocialInterpretation.event_candidate_status)).all():
                by_status[st] = cnt
        return {"total_interpretation_versions": total, "current_interpretations": current,
                "event_bearing": event_bearing, "unprocessed_evidence": unprocessed,
                "by_candidate_status": by_status, "classifier_version": C.CLASSIFIER_VERSION}
