from datetime import UTC, datetime

from crawl_service.entity_resolution import normalizers as N
from crawl_service.entity_resolution import resolvers as R
from crawl_service.entity_resolution.evidence import (
    ARTIST,
    EVENT_SERIES,
    ORGANIZER,
    VENUE,
    EntityEvidence,
)

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _ev(entity_type, source, raw, normalized=None, **evidence):
    return EntityEvidence(
        entity_type=entity_type, source=source, source_record_id=f"{source}-rec",
        canonical_event_id=f"event:{source}", source_entity_handle=f"{source}:{entity_type.lower()}:{N.slug(raw)}",
        raw_name=raw, normalized_name=normalized if normalized is not None else N.slug(raw).replace("-", " "),
        observed_at=NOW, confidence=0.8, evidence=evidence)


# ---- artist -------------------------------------------------------------------------------------
def test_artist_source_handle_match():
    ev = _ev(ARTIST, "district", "Prateek Kuhad", "prateek kuhad")
    known = R.KnownEntities(handle_map={(ev.source, ev.source_entity_handle): "artist:prateek-kuhad"})
    r = R.resolve_artist(ev, known)
    assert r.status == R.RESOLVED and r.reason_code == R.SOURCE_HANDLE_MATCH


def test_artist_same_name_across_sources_converges():
    a = _ev(ARTIST, "boshow", "Prateek Kuhad", "prateek kuhad")
    r1 = R.resolve_artist(a, R.KnownEntities())
    assert r1.reason_code == R.NEW_CANONICAL_ENTITY_CREATED and r1.created_new
    # second source, name now known -> resolves to the same canonical id
    known = R.KnownEntities(name_map={"prateek kuhad": {r1.canonical_entity_id}})
    b = _ev(ARTIST, "district", "Prateek Kuhad", "prateek kuhad")
    r2 = R.resolve_artist(b, known)
    assert r2.status == R.RESOLVED and r2.canonical_entity_id == r1.canonical_entity_id
    assert r2.reason_code == R.EXACT_UNIQUE_ALIAS and not r2.created_new


def test_artist_ambiguous_name_does_not_auto_resolve():
    ev = _ev(ARTIST, "boshow", "King", "king", is_ambiguous=True)
    r = R.resolve_artist(ev, R.KnownEntities())
    assert r.status == R.AMBIGUOUS and r.canonical_entity_id is None


def test_tribute_does_not_resolve_to_original():
    original = R.resolve_artist(_ev(ARTIST, "boshow", "Nirvana", "nirvana"), R.KnownEntities())
    trib = _ev(ARTIST, "district", "Nirvana Tribute Band", "nirvana tribute", is_tribute=True)
    r = R.resolve_artist(trib, R.KnownEntities(name_map={"nirvana": {original.canonical_entity_id}}))
    assert r.canonical_entity_id != original.canonical_entity_id
    assert R.TRIBUTE_OR_COVER_ACT in r.contradicting


def test_artist_new_entity_idempotent():
    ev = _ev(ARTIST, "boshow", "Some New Act", "some new act")
    r1 = R.resolve_artist(ev, R.KnownEntities())
    r2 = R.resolve_artist(ev, R.KnownEntities(name_map={"some new act": {r1.canonical_entity_id}}))
    assert r1.canonical_entity_id == r2.canonical_entity_id


# ---- venue --------------------------------------------------------------------------------------
def test_venue_same_name_city_resolves():
    a = R.resolve_venue(_ev(VENUE, "boshow", "Phoenix Marketcity", "phoenix marketcity", city="Mumbai"),
                        R.KnownEntities())
    known = R.KnownEntities(venue_map={"phoenix marketcity": [(a.canonical_entity_id, "Mumbai")]})
    b = R.resolve_venue(_ev(VENUE, "district", "Phoenix Marketcity", "phoenix marketcity", city="Mumbai"), known)
    assert b.status == R.RESOLVED and b.canonical_entity_id == a.canonical_entity_id
    assert b.reason_code == R.NAME_AND_CITY_MATCH


def test_venue_same_name_different_city_is_distinct():
    a = R.resolve_venue(_ev(VENUE, "boshow", "Phoenix Marketcity", "phoenix marketcity", city="Mumbai"),
                        R.KnownEntities())
    known = R.KnownEntities(venue_map={"phoenix marketcity": [(a.canonical_entity_id, "Mumbai")]})
    b = R.resolve_venue(_ev(VENUE, "district", "Phoenix Marketcity", "phoenix marketcity", city="Pune"), known)
    assert b.canonical_entity_id != a.canonical_entity_id
    assert "DIFFERENT_LOCATION" in b.supporting


def test_generic_venue_no_city_is_ambiguous():
    r = R.resolve_venue(_ev(VENUE, "boshow", "Town Hall", "town hall", is_generic=True), R.KnownEntities())
    assert r.status == R.AMBIGUOUS and r.reason_code == R.GENERIC_NAME


def test_generic_venue_with_city_is_possible_only():
    r = R.resolve_venue(_ev(VENUE, "boshow", "Town Hall", "town hall", is_generic=True, city="Kolkata"),
                        R.KnownEntities())
    assert r.status == R.POSSIBLE_MATCH


def test_venue_without_geography_unresolved_then_resolves():
    first = R.resolve_venue(_ev(VENUE, "boshow", "Skinny Mos", "skinny mos"), R.KnownEntities())
    assert first.status == R.UNRESOLVED and first.reason_code == R.VENUE_HAS_NO_GEOGRAPHY
    later = R.resolve_venue(_ev(VENUE, "boshow", "Skinny Mos", "skinny mos", city="Kolkata"), R.KnownEntities())
    assert later.status == R.RESOLVED and later.canonical_entity_id is not None


def test_venue_chain_locations_stay_distinct():
    m = R.resolve_venue(_ev(VENUE, "district", "Hard Rock Cafe", "hard rock cafe", city="Mumbai"),
                        R.KnownEntities())
    known = R.KnownEntities(venue_map={"hard rock cafe": [(m.canonical_entity_id, "Mumbai")]})
    h = R.resolve_venue(_ev(VENUE, "district", "Hard Rock Cafe", "hard rock cafe", city="Hyderabad"), known)
    assert m.canonical_entity_id != h.canonical_entity_id


# ---- organizer ---------------------------------------------------------------------------------
def test_organizer_exact_normalized_converges():
    a = R.resolve_organizer(_ev(ORGANIZER, "boshow", "Sunburn Productions", "sunburn"), R.KnownEntities())
    b = R.resolve_organizer(_ev(ORGANIZER, "district", "Sunburn", "sunburn"),
                            R.KnownEntities(name_map={"sunburn": {a.canonical_entity_id}}))
    assert b.status == R.RESOLVED and b.canonical_entity_id == a.canonical_entity_id


def test_organizer_source_handle_match():
    ev = _ev(ORGANIZER, "district", "NH7", "nh7")
    r = R.resolve_organizer(ev, R.KnownEntities(handle_map={(ev.source, ev.source_entity_handle): "organizer:nh7"}))
    assert r.reason_code == R.SOURCE_HANDLE_MATCH


def test_organizer_generic_is_ambiguous():
    r = R.resolve_organizer(_ev(ORGANIZER, "boshow", "Events", "events"), R.KnownEntities())
    assert r.status == R.AMBIGUOUS


# ---- event series -------------------------------------------------------------------------------
def test_series_editions_link_same_series():
    a = R.resolve_series(_ev(EVENT_SERIES, "boshow", "Robibarer Barbela Edition 1", "robibarer barbela",
                             organizer="Gram Art", edition_number=1), R.KnownEntities())
    known = R.KnownEntities(name_map={"robibarer barbela": {a.canonical_entity_id}})
    b = R.resolve_series(_ev(EVENT_SERIES, "boshow", "Robibarer Barbela Edition 2", "robibarer barbela",
                             organizer="Gram Art", edition_number=2), known)
    assert b.canonical_entity_id == a.canonical_entity_id


def test_series_different_years_link():
    a = R.resolve_series(_ev(EVENT_SERIES, "district", "Ziro Festival 2024", "ziro festival",
                             organizer="ZFM"), R.KnownEntities())
    b = R.resolve_series(_ev(EVENT_SERIES, "district", "Ziro Festival 2025", "ziro festival",
                             organizer="ZFM"), R.KnownEntities(name_map={"ziro festival": {a.canonical_entity_id}}))
    assert b.canonical_entity_id == a.canonical_entity_id


def test_series_generic_title_not_linked():
    r = R.resolve_series(_ev(EVENT_SERIES, "boshow", "Saturday Night", "saturday night", is_generic=True),
                         R.KnownEntities())
    assert r.status == R.AMBIGUOUS


def test_series_same_title_different_organizer_not_linked():
    a = R.resolve_series(_ev(EVENT_SERIES, "boshow", "Jazz Nights", "jazz nights", organizer="Blue Note"),
                         R.KnownEntities())
    b = R.resolve_series(_ev(EVENT_SERIES, "district", "Jazz Nights", "jazz nights", organizer="Other Org"),
                         R.KnownEntities(name_map={"jazz nights": {a.canonical_entity_id}}))
    assert b.canonical_entity_id != a.canonical_entity_id
