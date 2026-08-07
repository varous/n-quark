"""Google Trends (Phase 5A §11-§14/§24): API-disabled, import parse, term-vs-topic, geography,
normalization metadata, independent scales not merged, import idempotency, malformed rejection."""

import pytest
from sqlalchemy import select

from artist_intelligence_service.models import ArtistDemandObservation as ADO
from artist_intelligence_service.providers.base import ProviderAccessUnavailable
from artist_intelligence_service.providers.google_trends import (
    GoogleTrendsProvider,
    TrendsImportError,
    parse_trends_csv,
)
from artist_intelligence_service.service import DemandService

ARTIST = "artist:arijit-singh"

GEO_CSV = ("Category: All categories\n\nRegion,arijit singh: (India)\n"
           "West Bengal,100\nTripura,96\nMaharashtra,74\n")
TS_CSV = "Week,arijit singh: (India)\n2026-06-01,60\n2026-07-01,80\n2026-08-01,95\n"


async def test_official_api_access_unavailable():
    prov = GoogleTrendsProvider()
    assert prov.available is False
    status = DemandService().trends_official_status()
    assert status["status"] == "ACCESS_UNAVAILABLE"
    with pytest.raises(ProviderAccessUnavailable):
        await prov.get_geographic_interest("mid", region="IN")


def test_import_geo_parsing_and_iso_geography(db):
    out = DemandService().import_trends(db, ARTIST, csv_text=GEO_CSV, identity_type="SEARCH_TERM",
                                        provider_id="arijit singh", geo="IN", source_file="wb.csv")
    assert out["kind"] == "GEO"
    rows = db.execute(select(ADO).where(ADO.scope_type == "REGION")).scalars().all()
    by_label = {r.scope_label: r for r in rows}
    assert by_label["West Bengal"].scope_id == "IN-WB"       # ISO mapped, first-class geography
    assert by_label["West Bengal"].value_numeric == 100


def test_timeseries_import(db):
    out = DemandService().import_trends(db, ARTIST, csv_text=TS_CSV, identity_type="SEARCH_TERM",
                                        provider_id="arijit singh", geo="IN")
    assert out["kind"] == "TIMESERIES"
    rows = db.execute(select(ADO).where(ADO.scope_type == "COUNTRY")).scalars().all()
    assert len(rows) == 3
    assert all(r.provider_timestamp is not None for r in rows)


def test_search_term_vs_topic_not_combined(db):
    svc = DemandService()
    svc.import_trends(db, ARTIST, csv_text=TS_CSV, identity_type="SEARCH_TERM",
                      provider_id="arijit singh", geo="IN", source_file="term.csv")
    svc.import_trends(db, ARTIST, csv_text=TS_CSV, identity_type="TOPIC_ID",
                      provider_id="/m/08hr72", geo="IN", source_file="topic.csv")
    bases = {r.provenance.get("identity_basis") for r in
             db.execute(select(ADO)).scalars()}
    assert bases == {"SEARCH_TERM_BASED", "TOPIC_BASED"}     # distinct histories, not merged


def test_normalization_metadata_preserved(db):
    DemandService().import_trends(db, ARTIST, csv_text=GEO_CSV, identity_type="SEARCH_TERM",
                                  provider_id="arijit singh", geo="IN",
                                  comparison_window="2026-06..2026-08", source_file="wb.csv")
    row = db.execute(select(ADO)).scalars().first()
    p = row.provenance
    assert p["normalization"] == "trends_0_100_within_pull"
    assert p["comparison_window"] == "2026-06..2026-08"
    assert p["provider_mode"] == "IMPORT"
    assert "not comparable across pulls" in p["scale_note"]


def test_independent_exports_not_silently_merged(db):
    """Two exports with different comparison windows/source files stay distinct (different fingerprint)."""
    svc = DemandService()
    svc.import_trends(db, ARTIST, csv_text=GEO_CSV, identity_type="SEARCH_TERM",
                      provider_id="arijit singh", geo="IN", comparison_window="W1", source_file="a.csv")
    svc.import_trends(db, ARTIST, csv_text=GEO_CSV, identity_type="SEARCH_TERM",
                      provider_id="arijit singh", geo="IN", comparison_window="W2", source_file="b.csv")
    wb = db.execute(select(ADO).where(ADO.scope_label == "West Bengal")).scalars().all()
    assert len(wb) == 2                                     # both retained, not collapsed
    assert {w.provenance.get("comparison_window") for w in wb} == {"W1", "W2"}


def test_repeated_import_idempotent(db):
    svc = DemandService()
    a = svc.import_trends(db, ARTIST, csv_text=GEO_CSV, identity_type="SEARCH_TERM",
                          provider_id="arijit singh", geo="IN", source_file="wb.csv")
    b = svc.import_trends(db, ARTIST, csv_text=GEO_CSV, identity_type="SEARCH_TERM",
                          provider_id="arijit singh", geo="IN", source_file="wb.csv")
    assert a["observations"]["created"] == 3
    assert b["observations"]["created"] == 0                # same export fingerprint → idempotent


def test_malformed_import_rejected(db):
    with pytest.raises(TrendsImportError):
        parse_trends_csv("this is not,a trends export\nfoo,bar\n")
    with pytest.raises(ValueError):
        DemandService().import_trends(db, ARTIST, csv_text="garbage", identity_type="SEARCH_TERM",
                                      provider_id="x", geo="IN")
