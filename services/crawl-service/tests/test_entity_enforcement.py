"""Phase 5B.2.4 — interpretation enforcement + governed corrections (service-level, with a real DB)."""

import pytest
from crawl_service.entity_resolution import resolvers as R
from crawl_service.entity_resolution.service import EntityResolutionService
from crawl_service.models import EntityResolutionCandidate, EntityResolutionHistory


class FakeGraphReader:
    def __init__(self, node, neighbors):
        self._node, self._neighbors = node, neighbors
    async def get_event(self, cid):
        return self._node, self._neighbors


class FakeGraphWriter:
    def __init__(self):
        self.nodes, self.edges = [], []
    async def upsert_batch(self, nodes, edges):
        self.nodes += nodes; self.edges += edges


def _event_node(neighbors, *, organizer=None):
    props = {"display_name": "Some Show", "city": "Kolkata"}
    if organizer:
        props["organizer"] = organizer
    return {"id": "event:1", "properties": props}, neighbors


def _svc(db_session_factory, node, neighbors):
    return EntityResolutionService(db_session_factory, FakeGraphReader(node, neighbors), FakeGraphWriter())


def _feat(name):
    return {"relationship": "FEATURES", "node": {"id": f"src:artist:{name}", "properties": {"display_name": name}}}


def _venue(name):
    return {"relationship": "OCCURS_AT", "node": {"id": f"src:venue:{name}", "properties": {"display_name": name}}}


@pytest.mark.asyncio
async def test_placeholder_venue_never_creates_candidate(session_factory):
    node, nb = _event_node([_venue("Venue to be announced"), _feat("Prateek Kuhad")])
    svc = _svc(session_factory, node, nb)
    await svc.resolve_event(canonical_event_id="event:1", source="boshow", source_record_id="r1")
    with session_factory() as s:
        venue_cands = s.query(EntityResolutionCandidate).filter_by(entity_type="VENUE").all()
    # the placeholder produced NO venue candidate at all (suppressed at extraction)
    assert venue_cands == []


@pytest.mark.asyncio
async def test_compound_artist_split_no_combined_canonical(session_factory):
    node, nb = _event_node([_feat("Anuv Jain, Prateek Kuhad, Hanumankind")])
    svc = _svc(session_factory, node, nb)
    await svc.resolve_event(canonical_event_id="event:1", source="boshow", source_record_id="r1")
    with session_factory() as s:
        arts = {c.raw_name for c in s.query(EntityResolutionCandidate).filter_by(entity_type="ARTIST").all()}
    assert "Anuv Jain" in arts and "Prateek Kuhad" in arts and "Hanumankind" in arts
    assert "Anuv Jain, Prateek Kuhad, Hanumankind" not in arts   # no combined canonical


@pytest.mark.asyncio
async def test_cross_type_conflict_gated_to_review(session_factory):
    # seed a VENUE canonical "Skinny Mos", then resolve an event with an ARTIST mention of the same name
    node1, nb1 = _event_node([_venue("Skinny Mos")])
    await _svc(session_factory, node1, nb1).resolve_event(
        canonical_event_id="event:v", source="boshow", source_record_id="rv")
    node2, nb2 = _event_node([_feat("Skinny Mos")])
    await _svc(session_factory, node2, nb2).resolve_event(
        canonical_event_id="event:a", source="district", source_record_id="ra")
    with session_factory() as s:
        art = s.query(EntityResolutionCandidate).filter_by(entity_type="ARTIST", raw_name="Skinny Mos").one()
    assert art.resolution_status == R.ROLE_CONFLICT
    assert art.candidate_canonical_entity_id is None       # no wrong-type ARTIST canonical created


@pytest.mark.asyncio
async def test_quarantine_suppresses_from_product_and_audits(session_factory):
    node, nb = _event_node([_venue("Real Venue Hall"), _feat("Real Artist Person")])
    svc = _svc(session_factory, node, nb)
    await svc.resolve_event(canonical_event_id="event:1", source="boshow", source_record_id="r1")
    with session_factory() as s:
        v = s.query(EntityResolutionCandidate).filter_by(entity_type="VENUE").first()
        cid = v.candidate_canonical_entity_id
    # it is a normal product Venue before repair
    assert any(r["canonical_entity_id"] == cid for r in svc.entities(entity_type="VENUE")["entities"])
    out = svc.quarantine_canonical(cid, reason="test placeholder", actor="op@x.com")
    assert out["candidates_quarantined"] >= 1 and out["rows_deleted"] == 0
    # after repair: gone from product, but the row + history survive (evidence preserved)
    assert not any(r["canonical_entity_id"] == cid for r in svc.entities(entity_type="VENUE")["entities"])
    assert any(r["canonical_entity_id"] == cid for r in svc.entities(entity_type="VENUE", include_flagged=True)["entities"])
    with session_factory() as s:
        assert s.query(EntityResolutionCandidate).filter_by(candidate_canonical_entity_id=cid).count() >= 1
        assert s.query(EntityResolutionHistory).filter_by(reason_code="QUARANTINE").count() >= 1


@pytest.mark.asyncio
async def test_confirm_multi_role_is_audited(session_factory):
    node1, nb1 = _event_node([_venue("Dual Role Place")])
    await _svc(session_factory, node1, nb1).resolve_event(
        canonical_event_id="event:v", source="boshow", source_record_id="rv", now=None)
    node2, nb2 = _event_node([], organizer="Dual Role Place")
    svc = _svc(session_factory, node2, nb2)
    await svc.resolve_event(canonical_event_id="event:o", source="boshow", source_record_id="ro")
    with session_factory() as s:
        org = s.query(EntityResolutionCandidate).filter_by(entity_type="ORGANIZER", raw_name="Dual Role Place").one()
    assert org.resolution_status == R.ROLE_CONFLICT
    out = svc.apply_correction(action="CONFIRM_MULTI_ROLE", actor="op@x.com", candidate_id=org.id,
                               reason="venue also organizes")
    assert out["new_state"] == "LEGITIMATE_MULTI_ROLE"
    with session_factory() as s:
        org2 = s.get(EntityResolutionCandidate, org.id)
        assert org2.evidence.get("multi_role_confirmed") is True
        assert org2.evidence["corrections"][-1]["actor"] == "op@x.com"
