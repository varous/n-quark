from crawl_service.entity_resolution import normalizers as N


# ---- artist -------------------------------------------------------------------------------------
def test_artist_spelling_punctuation_diacritics_converge():
    assert N.normalize_artist("Prateek Kuhad").normalized == N.normalize_artist("prateek  kuhad!").normalized
    assert N.normalize_artist("Beyoncé").normalized == N.normalize_artist("Beyonce").normalized


def test_artist_feat_stripped():
    a = N.normalize_artist("Prateek Kuhad feat. Some Guest")
    assert a.normalized == "prateek kuhad" and a.stripped_feat is True


def test_artist_live_tail_stripped():
    assert N.normalize_artist("Prateek Kuhad Live in Concert").normalized == "prateek kuhad"


def test_tribute_not_collapsed_into_original():
    original = N.normalize_artist("Nirvana")
    tribute = N.normalize_artist("Nirvana Tribute Band")
    assert tribute.is_tribute is True
    assert tribute.normalized != original.normalized      # must not collapse
    assert "tribute" in tribute.normalized


def test_ambiguous_artist_flagged():
    assert N.is_ambiguous_artist(N.normalize_artist("King")) is True
    assert N.is_ambiguous_artist(N.normalize_artist("The Local Train")) is True
    assert N.is_ambiguous_artist(N.normalize_artist("Prateek Kuhad")) is False


# ---- venue --------------------------------------------------------------------------------------
def test_venue_aliases_converge():
    assert N.normalize_venue("The Phoenix Marketcity").normalized == N.normalize_venue("Phoenix Marketcity").normalized


def test_generic_venue_names_flagged():
    for name in ("Town Hall", "Community Hall", "The Club", "Auditorium", "Open Air Theatre"):
        assert N.normalize_venue(name).is_generic is True
    assert N.normalize_venue("Phoenix Marketcity").is_generic is False


# ---- organizer ---------------------------------------------------------------------------------
def test_organizer_company_suffixes_stripped():
    assert N.normalize_organizer("BookMyShow Pvt Ltd").normalized == N.normalize_organizer("BookMyShow").normalized
    assert N.normalize_organizer("Sunburn Productions").normalized == "sunburn"
    assert N.normalize_organizer("NH7 Events").normalized == "nh7"


# ---- event series -------------------------------------------------------------------------------
def test_series_edition_number_removed_but_preserved():
    s = N.normalize_series("Robibarer Barbela — Edition 2")
    assert s.series_normalized == "robibarer barbela"
    assert s.edition_number == 2 and "edition" in (s.edition_label or "").lower()


def test_series_year_suffix_removed():
    a = N.normalize_series("Ziro Festival 2024")
    b = N.normalize_series("Ziro Festival 2025")
    assert a.series_normalized == b.series_normalized == "ziro festival"
    assert a.edition_label == "2024" and b.edition_label == "2025"


def test_series_roman_edition():
    s = N.normalize_series("The Abomination XII")
    assert s.series_normalized == "the abomination" and s.edition_number == 12


def test_generic_recurring_title_flagged():
    for t in ("Saturday Night", "Open Mic", "Live Music", "Comedy Night"):
        assert N.normalize_series(t).is_generic is True
