"""Phase 5B.2.3 — role-aware mention interpretation (placeholder / compound / cross-type).

Deterministic, evidence-bounded. AI adjudication is a disabled seam here — ambiguous cases become
REVIEW_REQUIRED, never silently split or typed.
"""

from crawl_service.entity_resolution import compound as C
from crawl_service.entity_resolution import interpretation as I
from crawl_service.entity_resolution import placeholders as P
from crawl_service.entity_resolution.adjudicator import DisabledAdjudicator, get_adjudicator


# ---- placeholder classifier --------------------------------------------------------------------
def test_venue_to_be_announced_is_placeholder():
    assert P.classify_placeholder("Venue to be announced").is_placeholder is True


def test_common_tba_variants_are_placeholders():
    for s in ["TBA", "TBD", "To Be Announced", "To be confirmed", "Coming soon", "N/A", "Unknown",
              "Venue TBA", "Artist: TBA", "Lineup to be announced", "yet to be announced"]:
        assert P.classify_placeholder(s).is_placeholder is True, s


def test_legitimate_name_with_common_word_is_not_placeholder():
    for s in ["The Coming Soon Collective", "Unknown Mortal Orchestra", "Nirvana", "antiSOCIAL",
              "Arijit Singh"]:
        assert P.classify_placeholder(s).is_placeholder is False, s


# ---- compound parser ---------------------------------------------------------------------------
KNOWN = frozenset({"arijit singh", "shreya ghoshal", "simon garfunkel", "earth wind fire"})


def test_compound_splits_when_parts_known():
    r = C.parse_compound("Arijit Singh, Shreya Ghoshal", known_names=KNOWN)
    assert r.kind == C.COMPOUND and len(r.parts) == 2


def test_known_band_name_with_ampersand_not_split():
    # the WHOLE string is a known canonical → never split
    r = C.parse_compound("Simon & Garfunkel", known_names=KNOWN)
    assert r.kind == C.SINGLE


def test_delimiter_without_corroboration_is_ambiguous():
    r = C.parse_compound("Some Act & Another Thing", known_names=frozenset())
    assert r.kind == C.AMBIGUOUS


def test_no_delimiter_is_single():
    assert C.parse_compound("Prateek Kuhad", known_names=frozenset()).kind == C.SINGLE


def test_comma_lineup_of_three_is_compound():
    r = C.parse_compound("A One, B Two, C Three", known_names=frozenset())
    assert r.kind == C.COMPOUND and len(r.parts) == 3


# ---- interpretation orchestrator ---------------------------------------------------------------
def _known():
    return {"ARTIST": KNOWN}


def test_interpret_placeholder_venue_suppressed():
    out = I.interpret_mention(raw="Venue to be announced", expected_role="VENUE")
    assert out.outcome == I.PLACEHOLDER
    assert out.evidence["raw"] == "Venue to be announced"       # raw preserved as evidence


def test_interpret_compound_artists_split():
    out = I.interpret_mention(raw="Arijit Singh, Shreya Ghoshal", expected_role="ARTIST",
                              known_by_type=_known())
    assert out.outcome == I.COMPOUND_SPLIT and out.parts == ["Arijit Singh", "Shreya Ghoshal"]


def test_interpret_ambiguous_compound_is_review():
    out = I.interpret_mention(raw="Mystery Act & Other Thing", expected_role="ARTIST", known_by_type={})
    assert out.outcome == I.REVIEW_REQUIRED


def test_venue_in_artist_field_is_cross_type_conflict():
    # "antisocial" already exists as a VENUE; a mention of it in the ARTIST field must not create an Artist
    idx = {"antisocial": {"VENUE"}}       # keyed by slug()
    out = I.interpret_mention(raw="antiSOCIAL", expected_role="ARTIST", cross_type_index=idx)
    assert out.outcome == I.CROSS_TYPE_CONFLICT and "VENUE" in out.conflict_types


def test_organizer_in_artist_field_is_cross_type_conflict():
    idx = {"bookmyshow-live": {"ORGANIZER"}}
    out = I.interpret_mention(raw="BookMyShow Live", expected_role="ARTIST", cross_type_index=idx)
    assert out.outcome == I.CROSS_TYPE_CONFLICT and "ORGANIZER" in out.conflict_types


def test_obvious_single_passes_through_without_ai():
    out = I.interpret_mention(raw="Prateek Kuhad", expected_role="ARTIST", known_by_type={},
                              adjudicator=DisabledAdjudicator())
    assert out.outcome == I.OK and out.ai_assisted is False


def test_adjudicator_disabled_by_default():
    adj = get_adjudicator(None)
    assert adj.available is False
    # ambiguous case still resolves to review without the adjudicator
    out = I.interpret_mention(raw="X & Y", expected_role="ARTIST", adjudicator=adj)
    assert out.outcome == I.REVIEW_REQUIRED


# ---- pipeline integration (extract_event_entities) ---------------------------------------------
from datetime import UTC, datetime  # noqa: E402

from crawl_service.entity_resolution.evidence import extract_event_entities  # noqa: E402


def _event(*, venue=None, artists=(), organizer=None):
    node = {"id": "ev:1", "properties": {"display_name": "Some Show", "city": "Kolkata",
                                          **({"organizer": organizer} if organizer else {})}}
    neighbors = []
    if venue:
        neighbors.append({"relationship": "OCCURS_AT", "node": {"id": "v:1", "properties": {"display_name": venue}}})
    for a in artists:
        neighbors.append({"relationship": "FEATURES", "node": {"id": f"a:{a}", "properties": {"display_name": a}}})
    return extract_event_entities(canonical_event_id="ev:1", source="boshow", source_record_id="r1",
                                  node=node, neighbors=neighbors, observed_at=datetime.now(UTC))


def test_pipeline_placeholder_venue_not_created():
    e = _event(venue="Venue to be announced", artists=["Prateek Kuhad"])
    assert e.venue is None
    assert any(s["entity_type"] == "VENUE" for s in e.suppressed)


def test_pipeline_compound_artist_split_into_mentions():
    e = _event(artists=["Anuv Jain, Prateek Kuhad, Hanumankind"])
    names = sorted(a.raw_name for a in e.artists)
    assert names == ["Anuv Jain", "Hanumankind", "Prateek Kuhad"]
    assert e.compound_splits and e.compound_splits[0]["parts"]


def test_pipeline_single_artist_unchanged():
    e = _event(artists=["Prateek Kuhad"])
    assert len(e.artists) == 1 and e.artists[0].raw_name == "Prateek Kuhad"


def test_pipeline_placeholder_organizer_suppressed():
    e = _event(artists=["X Real Artist"], organizer="TBA")
    assert e.organizer is None
