"""Ticketing quality validation — rejection rules, geography, date/timezone, field status."""

from datetime import UTC, datetime

from signal_service.adapters import quality
from signal_service.adapters.quality import validate_ticketing_event
from signal_service.adapters.ticketing import TicketingEvent

NOW = datetime(2026, 8, 5, tzinfo=UTC)


def _event(**over) -> TicketingEvent:
    base = dict(  # noqa: C408
                source="skillbox", source_event_id="E1", event_slug="kolkata-gig",
                event_name="Kolkata Indie Night", event_url="https://s/events/kolkata-gig",
                city="Kolkata", region="Kolkata", country="India", venue_name="Skinny Mos",
                artists=["Someone"], category="Music", language="", currency="INR",
                price_min=499.0, is_free=False, starts_at=datetime(2026, 9, 1, 20, tzinfo=UTC),
                capacity=None, tickets_sold=None, verified=True, image_url="https://cdn/p.jpg")
    base.update(over)
    return TicketingEvent(**base)


def test_good_event_accepted_and_geography_derived():
    v = validate_ticketing_event(_event(), now=NOW)
    assert v.accepted and v.rejections == []
    assert v.geography["city_status"] == "VERIFIED"
    assert v.geography["derived_region"] == "West Bengal"      # from the verified map
    assert v.geography["source_region"] == "Kolkata"           # direct-source kept separate
    assert v.field_status["city"].specific and v.field_status["venue"].specific


def test_multiple_cities_placeholder_rejected():
    v = validate_ticketing_event(_event(city="Mutiple Cities, India", venue_name="Mutiple Cities, India"), now=NOW)
    assert not v.accepted and quality.MULTIPLE_CITIES_PLACEHOLDER in v.rejections


def test_numeric_city_id_rejected():
    v = validate_ticketing_event(_event(city="10432", region="10432"), now=NOW)
    assert quality.NUMERIC_LOCATION_WITHOUT_MAPPING in v.rejections


def test_generic_location_without_venue_rejected():
    v = validate_ticketing_event(_event(city="Online", venue_name=""), now=NOW)
    assert quality.GENERIC_LOCATION in v.rejections


def test_generic_city_with_real_venue_not_geo_rejected():
    # a real venue rescues a generic city from GENERIC_LOCATION
    v = validate_ticketing_event(_event(city="India", venue_name="Skinny Mos"), now=NOW)
    assert quality.GENERIC_LOCATION not in v.rejections


def test_far_future_placeholder_date_rejected():
    v = validate_ticketing_event(_event(starts_at=datetime(2029, 7, 28, tzinfo=UTC)), now=NOW)
    assert quality.PLACEHOLDER_DATE in v.rejections and v.date["far_future_placeholder"]


def test_placeholder_title_rejected():
    v = validate_ticketing_event(_event(event_name="Test Event"), now=NOW)
    assert quality.PLACEHOLDER_EVENT in v.rejections


def test_spam_page_rejected():
    v = validate_ticketing_event(_event(event_name="Buy Followers Cheap SEO Service"), now=NOW)
    assert quality.SEO_OR_SPAM_PAGE in v.rejections


def test_missing_identity_rejected():
    v = validate_ticketing_event(_event(source_event_id="", event_slug=""), now=NOW)
    assert quality.MISSING_IDENTITY in v.rejections


def test_deleted_shell_rejected():
    v = validate_ticketing_event(_event(verified=False, starts_at=None, venue_name="",
                                        price_min=None, artists=[], city="Kolkata"), now=NOW)
    assert quality.DELETED_EVENT_SHELL in v.rejections


def test_naive_date_with_verified_city_gets_ist():
    ev = _event(starts_at=datetime(2026, 9, 1, 20))  # naive  # noqa: DTZ001
    v = validate_ticketing_event(ev, now=NOW)
    normalized = quality.normalize_timezone(ev, v.geography, warnings=[])
    assert normalized.tzinfo is not None and str(normalized.tzinfo) == "Asia/Kolkata"


def test_verified_city_id_map_from_source_only():
    from signal_service.adapters.skillbox_cities import SKILLBOX_CITY_IDS, verified_city
    assert verified_city("5") == ("Mumbai", "Maharashtra", "Asia/Kolkata")
    assert verified_city("1106620")[0] == "Bengaluru"
    assert verified_city("999999") is None and verified_city(None) is None
    # Kolkata was not observed in the probe → intentionally absent (no guessed id)
    assert "Kolkata" not in {name for name, _, _ in SKILLBOX_CITY_IDS.values()}


def test_city_id_corroborates_unverified_name():
    # an unmapped city NAME but a verified source city_id → VERIFIED_BY_ID + derived region/tz
    v = validate_ticketing_event(_event(city="Bombay", source_city_id="5"), now=NOW)
    assert v.geography["city_status"] == "VERIFIED_BY_ID"
    assert v.geography["derived_region"] == "Maharashtra"
    assert v.geography["timezone"] == "Asia/Kolkata"
    assert v.field_status["city"].specific


def test_naive_date_with_unverified_city_warns_not_inferred():
    ev = _event(city="Nowheresville", starts_at=datetime(2026, 9, 1, 20))  # naive  # noqa: DTZ001
    warnings: list[str] = []
    out = quality.normalize_timezone(ev, quality.classify_geography(ev), warnings=warnings)
    assert out.tzinfo is None and any("not inferred" in w for w in warnings)
