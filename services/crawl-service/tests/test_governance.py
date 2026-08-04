"""Governed entity-resolution commands (Admin Phase B): accept/reject/create/link/supersede/correct-
series/reverse, plus the year-only series safeguard. All reuse the Phase 3.1 pathways."""

from datetime import UTC, datetime

import pytest
from _stubs import MultiStubGraphReader, StubGraphWriter

from crawl_service.config import Settings
from crawl_service.db import SessionLocal
from crawl_service.entity_resolution.evidence import extract_event_entities
from crawl_service.governance import GovernanceConflict, GovernanceError, GovernanceService
from crawl_service.models import EntityResolutionCandidate, EntitySourceHandle, EntitySupersession

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _seed_candidate(cid="c1", *, entity_type="ARTIST", source="boshow", status="AMBIGUOUS",
                    handle="boshow:artist:pilu", raw="Pilu", norm="pilu", canonical=None,
                    event_id="event:x"):
    with SessionLocal() as s, s.begin():
        s.add(EntityResolutionCandidate(
            id=cid, entity_type=entity_type, source=source, source_record_id="r1",
            canonical_event_id=event_id, source_entity_handle=handle, raw_name=raw,
            normalized_name=norm, candidate_canonical_entity_id=canonical, match_score=0.4,
            resolution_status=status, reason_code="AMBIGUOUS_NAME", supporting_signals=[],
            contradicting_signals=[], evidence={}, resolver_version="v1", observed_at=NOW,
            created_at=NOW, updated_at=NOW))
    return cid


def _node(node_id, ntype):
    return ({"id": node_id, "type": ntype, "properties": {"display_name": node_id}}, [])


def _svc(mapping=None):
    return GovernanceService(SessionLocal, MultiStubGraphReader(mapping=mapping or {}),
                             StubGraphWriter(), Settings())


@pytest.mark.asyncio
async def test_accept_resolves_and_registers_handle():
    _seed_candidate()
    svc = _svc({"artist:pilu": _node("artist:pilu", "artist")})
    res = await svc.accept(candidate_id="c1", canonical_entity_id="artist:pilu")
    assert res["candidate"]["status"] == "RESOLVED"
    assert res["previous_status"] == "AMBIGUOUS"
    with SessionLocal() as s:
        from sqlalchemy import select
        owner = s.execute(select(EntitySourceHandle.canonical_entity_id).where(
            EntitySourceHandle.source_entity_handle == "boshow:artist:pilu")).scalar_one()
    assert owner == "artist:pilu"


@pytest.mark.asyncio
async def test_accept_idempotent():
    _seed_candidate()
    svc = _svc({"artist:pilu": _node("artist:pilu", "artist")})
    await svc.accept(candidate_id="c1", canonical_entity_id="artist:pilu")
    again = await svc.accept(candidate_id="c1", canonical_entity_id="artist:pilu")
    assert again.get("idempotent") is True


@pytest.mark.asyncio
async def test_accept_stale_preview_conflict():
    _seed_candidate(status="AMBIGUOUS")
    svc = _svc({"artist:pilu": _node("artist:pilu", "artist")})
    with pytest.raises(GovernanceConflict) as e:
        await svc.accept(candidate_id="c1", canonical_entity_id="artist:pilu", expected_status="UNRESOLVED")
    assert e.value.code == "STALE_PREVIEW"


@pytest.mark.asyncio
async def test_accept_type_mismatch():
    _seed_candidate()
    svc = _svc({"venue:pilu": _node("venue:pilu", "venue")})
    with pytest.raises(GovernanceError) as e:
        await svc.accept(candidate_id="c1", canonical_entity_id="venue:pilu")
    assert e.value.code == "ENTITY_TYPE_MISMATCH"


@pytest.mark.asyncio
async def test_link_handle_conflict():
    _seed_candidate()
    # pre-link the handle to a different canonical
    with SessionLocal() as s, s.begin():
        s.add(EntitySourceHandle(id="h1", entity_type="ARTIST", source="boshow",
                                 source_entity_handle="boshow:artist:pilu",
                                 canonical_entity_id="artist:other", confidence=0.9,
                                 resolution_method="X", first_seen=NOW, last_seen=NOW))
    svc = _svc({"artist:pilu": _node("artist:pilu", "artist")})
    with pytest.raises(GovernanceConflict) as e:
        await svc.accept(candidate_id="c1", canonical_entity_id="artist:pilu")
    assert e.value.code == "HANDLE_ALREADY_LINKED"


def test_reject_marks_rejected_and_keeps_evidence():
    _seed_candidate()
    svc = _svc()
    res = svc.reject(candidate_id="c1", reason_code="WRONG_ENTITY")
    assert res["candidate"]["status"] == "REJECTED"
    with SessionLocal() as s:
        c = s.get(EntityResolutionCandidate, "c1")
    assert c.reason_code == "WRONG_ENTITY" and c.raw_name == "Pilu"  # evidence preserved


@pytest.mark.asyncio
async def test_create_entity_generic_rejected():
    _seed_candidate(entity_type="VENUE", handle="boshow:venue:town-hall", raw="Town Hall", norm="town hall")
    svc = _svc()
    with pytest.raises(GovernanceError) as e:
        await svc.create_entity(entity_type="VENUE", canonical_name="Town Hall", candidate_id="c1",
                                city="Kolkata")
    # "Town Hall" is generic -> rejected even with a city
    assert e.value.code == "GENERIC_NAME"


@pytest.mark.asyncio
async def test_create_entity_venue_requires_city():
    _seed_candidate(entity_type="VENUE", handle="boshow:venue:x", raw="Skinny Mos", norm="skinny mos")
    svc = _svc()
    with pytest.raises(GovernanceError) as e:
        await svc.create_entity(entity_type="VENUE", canonical_name="Skinny Mos", candidate_id="c1")
    assert e.value.code == "VENUE_REQUIRES_CITY"


@pytest.mark.asyncio
async def test_create_entity_success():
    _seed_candidate(entity_type="ARTIST", handle="boshow:artist:new-act", raw="New Act", norm="new act")
    svc = _svc()
    res = await svc.create_entity(entity_type="ARTIST", canonical_name="New Act", candidate_id="c1")
    assert res["created_canonical_entity_id"] == "artist:new-act"
    assert res["candidate"]["status"] == "RESOLVED"


@pytest.mark.asyncio
async def test_supersede_legacy_and_counts():
    # a resolved candidate on the canonical + a legacy node superseded onto it
    _seed_candidate(entity_type="VENUE", status="RESOLVED", handle="boshow:venue:utp",
                    raw="Urban Theatre", norm="urban theatre project",
                    canonical="venue:urban-theatre-project--kolkata")
    svc = _svc({"venue:urban-theatre-project--kolkata":
                _node("venue:urban-theatre-project--kolkata", "venue")})
    res = await svc.supersede_legacy(entity_type="VENUE",
                                     legacy_entity_id="venue:the-urban-theatre-project",
                                     canonical_entity_id="venue:urban-theatre-project--kolkata")
    assert res["relationship"] == "SUPERSEDED_BY"
    with SessionLocal() as s:
        from sqlalchemy import select
        sup = s.execute(select(EntitySupersession)).scalar_one()
    assert sup.active is True and sup.legacy_entity_id == "venue:the-urban-theatre-project"
    counts = await svc.governance_counts()
    assert counts["legacy_superseded_nodes"] == 1


@pytest.mark.asyncio
async def test_correct_series_create_and_unlink():
    svc = _svc()
    # unlink an incorrect year-only series, then create a proper one
    res = await svc.correct_series(event_id="event:f1", mode="UNLINK", prev_series_id="series:f1")
    assert res["superseded"] == "series:f1"
    res2 = await svc.correct_series(event_id="event:x", mode="CREATE", series_name="The Abomination",
                                    organizer="TopCat")
    assert res2["created_canonical_entity_id"].startswith("series:the-abomination")


@pytest.mark.asyncio
async def test_reverse_accept_restores_status():
    _seed_candidate(status="AMBIGUOUS")
    svc = _svc({"artist:pilu": _node("artist:pilu", "artist")})
    await svc.accept(candidate_id="c1", canonical_entity_id="artist:pilu")
    rev = await svc.reverse_accept(candidate_id="c1", restore_status="AMBIGUOUS",
                                   restore_canonical=None, remove_handle=True)
    assert rev["candidate"]["status"] == "AMBIGUOUS"
    with SessionLocal() as s:
        from sqlalchemy import select
        owner = s.execute(select(EntitySourceHandle).where(
            EntitySourceHandle.source_entity_handle == "boshow:artist:pilu")).scalar_one_or_none()
    assert owner is None  # handle unlinked on reversal


# ---- series safeguard (evidence level) ----------------------------------------------------------
def _event(title):
    return ({"id": "event:e", "type": "event", "properties": {"display_name": title, "organizer": "Org"}}, [])


def test_year_only_title_produces_no_series_evidence():
    node, neigh = _event("F1 2026")
    ents = extract_event_entities(canonical_event_id="event:e", source="district",
                                  source_record_id="r", node=node, neighbors=neigh, observed_at=NOW)
    assert ents.series is None


def test_edition_title_produces_series_evidence():
    node, neigh = _event("THE ABOMINATION XII")
    ents = extract_event_entities(canonical_event_id="event:e", source="boshow",
                                  source_record_id="r", node=node, neighbors=neigh, observed_at=NOW)
    assert ents.series is not None and ents.series.evidence["edition_number"] == 12
