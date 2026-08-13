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


@pytest.mark.asyncio
async def test_established_same_type_not_suppressed_by_cross_type(session_factory):
    # VENUE "Multi Hall" gets established; an ORGANIZER "Multi Hall" also exists (dual role).
    await _svc(session_factory, *_event_node([_venue("Multi Hall")])).resolve_event(
        canonical_event_id="event:v1", source="boshow", source_record_id="rv1")
    await _svc(session_factory, *_event_node([], organizer="Multi Hall")).resolve_event(
        canonical_event_id="event:o1", source="boshow", source_record_id="ro1")  # organizer → ROLE_CONFLICT (first dual)
    # re-resolving the VENUE must STILL resolve it (established same-type canonical), not suppress it
    await _svc(session_factory, *_event_node([_venue("Multi Hall")])).resolve_event(
        canonical_event_id="event:v2", source="district", source_record_id="rv2")
    with session_factory() as s:
        venues = s.query(EntityResolutionCandidate).filter_by(entity_type="VENUE", raw_name="Multi Hall").all()
    assert any(v.resolution_status == R.RESOLVED and v.candidate_canonical_entity_id for v in venues)


@pytest.mark.asyncio
async def test_interpreted_event_venue_not_announced_after_quarantine(session_factory):
    node, nb = _event_node([_venue("Real Hall"), _feat("Real Artist")])
    svc = _svc(session_factory, node, nb)
    await svc.resolve_event(canonical_event_id="event:1", source="boshow", source_record_id="r1")
    with session_factory() as s:
        cid = s.query(EntityResolutionCandidate).filter_by(entity_type="VENUE").first().candidate_canonical_entity_id
    # before: venue PRESENT
    rel = svc.interpreted_relationships("event:1")
    assert rel["venue"]["state"] == "PRESENT" and rel["venue"]["canonical_entity_id"] == cid
    # quarantine → venue reads NOT_ANNOUNCED, no canonical, raw preserved
    svc.quarantine_canonical(cid, reason="placeholder", actor="op@x.com")
    rel2 = svc.interpreted_relationships("event:1")
    assert rel2["venue"]["state"] == "NOT_ANNOUNCED"
    assert rel2["venue"]["canonical_entity_id"] is None
    assert "Real Hall" in rel2["venue"]["raw_mentions"]      # raw source evidence preserved


@pytest.mark.asyncio
async def test_interpreted_event_role_conflict_needs_review(session_factory):
    await _svc(session_factory, *_event_node([_venue("Skinny Mos")])).resolve_event(
        canonical_event_id="event:v", source="boshow", source_record_id="rv")
    svc = _svc(session_factory, *_event_node([_feat("Skinny Mos")]))
    await svc.resolve_event(canonical_event_id="event:a", source="district", source_record_id="ra")
    rel = svc.interpreted_relationships("event:a")
    assert rel["artists"]["needs_review_count"] == 1
    assert rel["artists"]["resolved_count"] == 0            # no fake canonical link


@pytest.mark.asyncio
async def test_quality_metrics_counts_flow(session_factory):
    await _svc(session_factory, *_event_node([_venue("Venue to be announced"),
              _feat("Anuv Jain, Prateek Kuhad, Hanumankind")])).resolve_event(
        canonical_event_id="event:1", source="boshow", source_record_id="r1")
    m = EntityResolutionService(session_factory, FakeGraphReader(None, []), FakeGraphWriter()).quality_metrics()
    assert m["flow"]["compound_split"] >= 2                 # the two split artists
    assert m["interpretation_method"] == "deterministic" and m["ai_adjudicated"] == 0


def _seed_candidate(session_factory, *, name, cid, status, etype="VENUE", handle=None):
    from datetime import UTC, datetime
    import uuid
    from crawl_service.models import EntityResolutionCandidate
    now = datetime.now(UTC)
    with session_factory() as s:
        s.add(EntityResolutionCandidate(
            id=uuid.uuid4().hex, entity_type=etype, source="district",
            source_record_id="seed-r", canonical_event_id="event:seed",
            source_entity_handle=handle or f"district:{etype.lower()}:{name}",
            raw_name=name, normalized_name=name.lower(),
            candidate_canonical_entity_id=cid, match_score=1.0,
            resolution_status=status, reason_code="SEED", evidence={},
            resolver_version="test", observed_at=now, created_at=now, updated_at=now))
        s.commit()


@pytest.mark.asyncio
async def test_quality_audit_distinguishes_repaired_from_open(session_factory):
    svc = _svc(session_factory, *_event_node([]))
    # an OPEN placeholder (still resolved to a canonical) and a REPAIRED one (already quarantined)
    _seed_candidate(session_factory, name="TBA", cid="venue:tba-open", status=R.RESOLVED)
    _seed_candidate(session_factory, name="Venue to be announced", cid="venue:vtba-fixed",
                    status=R.QUARANTINED)
    audit = svc.quality_audit()
    by_id = {m["canonical_entity_id"]: m for m in audit["manifest"]}
    assert by_id["venue:tba-open"]["problem_class"] == "PLACEHOLDER_ENTITY"
    assert by_id["venue:tba-open"]["repaired"] is False and by_id["venue:tba-open"]["state"] == "open"
    assert by_id["venue:vtba-fixed"]["repaired"] is True and by_id["venue:vtba-fixed"]["state"] == "repaired"
    # the already-repaired one is NOT counted as an open issue
    assert audit["open_issues"] >= 1 and audit["repaired_issues"] >= 1
    assert audit["counts_by_problem"].get("PLACEHOLDER_ENTITY", 0) == 1   # open only
    assert audit["repaired_by_problem"].get("PLACEHOLDER_ENTITY", 0) == 1


@pytest.mark.asyncio
async def test_confirm_existing_match_links_selected_canonical(session_factory):
    svc = _svc(session_factory, *_event_node([]))
    # a real VENUE canonical exists in the registry
    _seed_candidate(session_factory, name="Kala Mandir", cid="venue:kala-mandir", status=R.RESOLVED)
    # a review mention with no canonical yet
    _seed_candidate(session_factory, name="Kala Mondir", cid=None, status=R.REVIEW_REQUIRED,
                    handle="district:venue:kala-mondir")
    from crawl_service.models import EntityResolutionCandidate
    with session_factory() as s:
        review = s.query(EntityResolutionCandidate).filter_by(raw_name="Kala Mondir").one()
        rid = review.id
    out = svc.apply_correction(action="CONFIRM_EXISTING_MATCH", actor="op@x.com",
                               candidate_id=rid, canonical_entity_id="venue:kala-mandir",
                               reason="same venue, spelling variant")
    assert out["new_state"] == R.RESOLVED and out["audited"] is True
    with session_factory() as s:
        c = s.get(EntityResolutionCandidate, rid)
        assert c.candidate_canonical_entity_id == "venue:kala-mandir"   # linked to the SELECTED canonical
        assert c.resolution_status == R.RESOLVED
        assert c.evidence["corrections"][-1]["new_canonical_entity_id"] == "venue:kala-mandir"


@pytest.mark.asyncio
async def test_confirm_existing_match_rejects_arbitrary_id(session_factory):
    svc = _svc(session_factory, *_event_node([]))
    _seed_candidate(session_factory, name="Some Place", cid=None, status=R.REVIEW_REQUIRED)
    from crawl_service.models import EntityResolutionCandidate
    with session_factory() as s:
        rid = s.query(EntityResolutionCandidate).filter_by(raw_name="Some Place").one().id
    # an id that is not a known canonical of the correct type is refused (no arbitrary linking)
    with pytest.raises(ValueError):
        svc.apply_correction(action="CONFIRM_EXISTING_MATCH", actor="op@x.com",
                             candidate_id=rid, canonical_entity_id="venue:totally-made-up")
