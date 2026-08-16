from crawl_service.entity_resolution.type_classifier import (
    AMBIGUOUS_TYPE, CLEAR_TYPE, ROLE_CONFLICT, classify_type,
)


def test_structured_source_roles_are_strong_evidence():
    assert classify_type(raw="Asha", requested_role="ARTIST", source_field="performer", schema_type="Person").predicted_type == "ARTIST"
    assert classify_type(raw="Town Hall", requested_role="VENUE", source_field="location", schema_type="Place").predicted_type == "VENUE"
    assert classify_type(raw="Acme", requested_role="ORGANIZER", source_field="organizer", schema_type="Organization").predicted_type == "ORGANIZER"


def test_vocabulary_variants_are_weighted_evidence():
    assert classify_type(raw="Acme Event Organisers Pvt Ltd", requested_role="ARTIST").predicted_type == "ORGANIZER"
    assert classify_type(raw="Civic Theatre", requested_role="VENUE").predicted_type == "VENUE"
    assert classify_type(raw="Rao Musician Duo", requested_role="ARTIST").predicted_type == "ARTIST"


def test_same_event_venue_blocks_silent_artist_creation_skinny_mos_regression():
    result = classify_type(raw="Skinny Mo's", requested_role="ARTIST", source_field="name_of_artist",
                           cohort_roles={"ARTIST", "VENUE"})
    assert result.outcome == ROLE_CONFLICT
    assert result.predicted_type == "VENUE"


def test_business_shape_blocks_silent_artist_creation_regression():
    result = classify_type(raw="India Business Helpline", requested_role="ARTIST", source_field="performer")
    assert result.outcome == AMBIGUOUS_TYPE
    assert result.predicted_type in {"ORGANIZER", "UNKNOWN"}


def test_exact_existing_other_type_is_strong_conflict():
    assert classify_type(raw="Known Place", requested_role="ARTIST", existing_types={"VENUE"}).outcome == ROLE_CONFLICT
    assert classify_type(raw="Known Company", requested_role="ARTIST", existing_types={"ORGANIZER"}).outcome == ROLE_CONFLICT


def test_terms_are_not_absolute_and_legitimate_artist_remains_possible():
    result = classify_type(raw="The Company Band", requested_role="ARTIST", source_field="performer", schema_type="MusicGroup")
    assert result.outcome == CLEAR_TYPE and result.predicted_type == "ARTIST"
    club = classify_type(raw="Two Door Cinema Club", requested_role="ARTIST", source_field="performer", schema_type="MusicGroup")
    assert club.outcome == CLEAR_TYPE and club.predicted_type == "ARTIST"


def test_confirmed_multi_role_can_remain_clear_for_requested_role():
    result = classify_type(raw="Studio Collective", requested_role="ARTIST", operator_confirmed_types={"ARTIST", "ORGANIZER"})
    assert result.predicted_type == "ARTIST"
