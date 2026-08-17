"""Governed wrong-type repair (Phase 5B.3.3).

A deterministic wrong-type historical canonical (e.g. a Boshow ``name_of_artist`` value that is really a
venue) is repaired only through governance: the wrong-typed canonical is QUARANTINED — evidence and
history preserved — while the corroborating correct-typed canonical stands, and the wrong interpretation
disappears from normal product cohorts. Corroboration is required, so vocabulary/role alone cannot retype.
"""
from datetime import UTC, datetime

import pytest
from _stubs import MultiStubGraphReader, StubGraphWriter

from crawl_service.config import Settings
from crawl_service.entity_resolution import resolvers as R
from crawl_service.entity_resolution.service import EntityResolutionService
from crawl_service.models import EntityResolutionCandidate, EntityResolutionHistory
from sqlalchemy import select

NOW = datetime(2026, 8, 17, tzinfo=UTC)


def _svc(session_factory):
    return EntityResolutionService(session_factory, MultiStubGraphReader(mapping={}),
                                   StubGraphWriter(), Settings())


def _seed(session_factory, *, canonical, etype, raw, status=R.RESOLVED, source_field=None,
          handle=None, record="rec-1"):
    with session_factory() as s, s.begin():
        s.add(EntityResolutionCandidate(
            id=f"{canonical}|{handle or raw}|{record}".replace(" ", "_")[:60],
            entity_type=etype, source="boshow", source_record_id=record,
            canonical_event_id="boshow:show:evt-1", source_entity_handle=handle or f"boshow:{raw}",
            raw_name=raw, normalized_name=raw.lower(), candidate_canonical_entity_id=canonical,
            match_score=0.9, resolution_status=status, reason_code="SEED",
            supporting_signals=[], contradicting_signals=[],
            evidence={"source_field": source_field} if source_field else {},
            resolver_version="seed", observed_at=NOW, created_at=NOW, updated_at=NOW))


def _seed_skinny(session_factory):
    # the corroborated, operator-confirmed VENUE and the wrong ARTIST (name_of_artist misfire)
    _seed(session_factory, canonical="venue:skinny-mos--kolkata", etype="VENUE", raw="Skinny Mos",
          source_field="location", handle="boshow:loc:skinny", record="rec-v")
    _seed(session_factory, canonical="artist:skinny-mos", etype="ARTIST", raw="Skinny Mos",
          source_field="name_of_artist", handle="boshow:art:skinny", record="rec-a")


def test_wrong_type_repair_requires_corroborating_canonical(session_factory):
    # only the ARTIST canonical exists — no established VENUE to corroborate → refused.
    _seed(session_factory, canonical="artist:skinny-mos", etype="ARTIST", raw="Skinny Mos",
          source_field="name_of_artist")
    svc = _svc(session_factory)
    with pytest.raises(ValueError, match="corroborating VENUE"):
        svc.apply_correction(action="MARK_WRONG_TYPE", actor="op", canonical_entity_id="artist:skinny-mos",
                             corrected_type="VENUE", now=NOW)


def test_wrong_type_repair_needs_corrected_type(session_factory):
    _seed_skinny(session_factory)
    svc = _svc(session_factory)
    with pytest.raises(ValueError, match="corrected_type required"):
        svc.apply_correction(action="MARK_WRONG_TYPE", actor="op",
                             canonical_entity_id="artist:skinny-mos", now=NOW)


def test_wrong_type_repair_quarantines_and_preserves_evidence(session_factory):
    _seed_skinny(session_factory)
    svc = _svc(session_factory)
    out = svc.apply_correction(action="MARK_WRONG_TYPE", actor="op",
                               reason="name_of_artist misfire; venue identity stands",
                               canonical_entity_id="artist:skinny-mos", corrected_type="VENUE", now=NOW)
    assert out["candidates_quarantined"] == 1
    assert out["rows_deleted"] == 0 and out["evidence_preserved"] is True
    assert out["correction_action"] == "MARK_WRONG_TYPE" and out["corrected_type"] == "VENUE"
    with session_factory() as s:
        art = s.execute(select(EntityResolutionCandidate).where(
            EntityResolutionCandidate.candidate_canonical_entity_id == "artist:skinny-mos")).scalars().all()
        ven = s.execute(select(EntityResolutionCandidate).where(
            EntityResolutionCandidate.candidate_canonical_entity_id == "venue:skinny-mos--kolkata")).scalars().all()
        hist = s.execute(select(EntityResolutionHistory)).scalars().all()
    assert all(c.resolution_status == R.QUARANTINED for c in art)          # wrong type suppressed
    assert all(c.resolution_status == R.RESOLVED for c in ven)             # correct type preserved
    corr = art[0].evidence["corrections"][-1]
    assert corr["action"] == "MARK_WRONG_TYPE" and corr["corrected_type"] == "VENUE"
    assert art[0].evidence["source_field"] == "name_of_artist"             # raw evidence kept
    assert any(h.reason_code == "WRONG_TYPE_QUARANTINE" for h in hist)     # audit history written


def test_wrong_type_repair_suppresses_from_product_but_keeps_correct(session_factory):
    _seed_skinny(session_factory)
    svc = _svc(session_factory)
    svc.apply_correction(action="MARK_WRONG_TYPE", actor="op", canonical_entity_id="artist:skinny-mos",
                         corrected_type="VENUE", now=NOW)
    product = {e["canonical_entity_id"] for e in svc.entities()["entities"]}
    flagged = {e["canonical_entity_id"] for e in svc.entities(include_flagged=True)["entities"]}
    assert "artist:skinny-mos" not in product                 # wrong interpretation gone from product
    assert "venue:skinny-mos--kolkata" in product             # correct identity remains
    assert "artist:skinny-mos" in flagged                     # still inspectable under Advanced/evidence


def test_repair_clears_cross_type_flag_off_correct_sibling(session_factory):
    _seed_skinny(session_factory)
    svc = _svc(session_factory)
    before = svc.quality_audit()
    assert before["counts_by_problem"].get("CROSS_TYPE_CONFLICT", 0) >= 2   # both sides flagged
    svc.apply_correction(action="MARK_WRONG_TYPE", actor="op", canonical_entity_id="artist:skinny-mos",
                         corrected_type="VENUE", now=NOW)
    after = svc.quality_audit()
    flagged = {(m["canonical_entity_id"], m["state"]) for m in after["manifest"]
               if m["problem_class"] == "CROSS_TYPE_CONFLICT"}
    # correct venue is no longer an OPEN conflict; the wrong artist remains only as repaired provenance
    assert ("venue:skinny-mos--kolkata", "open") not in flagged
    assert after["open_by_problem"].get("CROSS_TYPE_CONFLICT", 0) < before["open_by_problem"]["CROSS_TYPE_CONFLICT"]
    assert ("artist:skinny-mos", "repaired") in flagged


def test_legitimate_multi_role_is_not_repaired_and_stays_in_product(session_factory):
    # DORANGOS: structured venue AND structured organizer — both legitimate, both must remain.
    _seed(session_factory, canonical="venue:dorangos--mumbai", etype="VENUE", raw="DORANGOS",
          source_field="location", handle="d:loc", record="d-v")
    _seed(session_factory, canonical="organizer:dorangos", etype="ORGANIZER", raw="DORANGOS",
          source_field="organizer", handle="d:org", record="d-o")
    svc = _svc(session_factory)
    product = {e["canonical_entity_id"] for e in svc.entities()["entities"]}
    assert {"venue:dorangos--mumbai", "organizer:dorangos"} <= product      # multi-role intact
