from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from signal_service.adapters.ticketing import (
    AllEventsProvider,
    KnowafestProvider,
    LumaProvider,
    MeetupProvider,
    MockTicketingProvider,
    TownscriptProvider,
    _india_city_region,
    _jsonld_events,
    _jsonld_price,
    _maybe_gunzip,
    _sitemap_slugs,
    event_from_boshow,
    event_from_district,
    event_from_jsonld,
    event_from_knowafest,
    event_from_skillbox,
    event_from_townscript,
    get_provider,
    normalize_event,
    split_lineup,
    split_location,
)
from signal_service.config import settings
from signal_service.graph_projection import project_ticketing_graph
from signal_service.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_split_location_city_state_country() -> None:
    assert split_location("Kolkata-West Bengal-India") == ("Kolkata", "West Bengal", "India")
    assert split_location("Pune-Maharashtra-India") == ("Pune", "Maharashtra", "India")


def test_split_lineup_drops_generic_prefix_and_dedupes() -> None:
    assert split_lineup("Skinny Mos • Dolinman • Gaboo") == ["Skinny Mos", "Dolinman", "Gaboo"]
    # "Live at X" is a placeholder, not a performer — fall back to member_name
    assert split_lineup("Live at Skinny Mos", member_name="Skinny Mos") == ["Skinny Mos"]


def test_fill_ratio_is_the_demand_ground_truth() -> None:
    event = event_from_boshow(
        {
            "display_name": "ATSP", "show_type": "Performance Art",
            "name_of_artist": "At the Still Point", "location": "The Urban Theatre Project",
            "city": "Kolkata-West Bengal-India", "default_price": 600, "currency": "INR",
            "real_show_date": "2026-07-31T19:30:00.000Z", "gc": 49, "tickets_sold": 31,
            "show_id": ["256f6c6e"], "slug": "atsp",
        }
    )
    assert event.fill_ratio == round(31 / 49, 3)
    assert event.region == "West Bengal"
    assert event.price_min == 600.0


def test_fill_ratio_none_without_capacity() -> None:
    event = event_from_boshow({"display_name": "x", "city": "Kolkata-West Bengal-India", "slug": "x"})
    assert event.fill_ratio is None


async def test_normalize_emits_demand_and_relationship_observations() -> None:
    event = await MockTicketingProvider().extract("free-folk-nite-01082026")
    obs = normalize_event(event)
    attrs = {o.attribute for o in obs}
    assert {"fill_ratio", "occurs_at_venue", "lineup", "in_region", "source_event_id", "image_url"} <= attrs
    # everything keys on the type-neutral event handle
    assert all(o.entity == "boshow:show:a7ed0638-ef5e-4f98-801b-ad46e3a75a6d" for o in obs)
    fill = next(o for o in obs if o.attribute == "fill_ratio")
    assert fill.value == round(10 / 50, 3)
    assert fill.evidence["tickets_sold"] == 10 and fill.evidence["capacity"] == 50


async def test_normalize_provenance_is_compliant_public_scrape() -> None:
    event = await MockTicketingProvider().extract("jamsteady-with-cherry-mrong-31072026")
    prov = normalize_event(event)[0].metadata["provenance"]
    assert prov["acquisition_method"] == "public_scrape"
    assert prov["logged_out"] is True and prov["robots_respected"] is True
    assert prov["contains_pii"] is False


def test_project_ticketing_graph_builds_structural_edges() -> None:
    projection = project_ticketing_graph(
        event_id="event:free-folk-nite",
        event_properties={"fill_ratio": 0.2, "category": "Music"},
        venue_id="venue:skinny-mos",
        venue_name="Skinny Mos",
        artists=[("artist:skinny-mos", "Skinny Mos"), ("artist:dolinman", "Dolinman")],
        region="West Bengal",
    )
    rels = {(e.source, e.relationship, e.target) for e in projection.edges}
    assert ("event:free-folk-nite", "OCCURS_AT", "venue:skinny-mos") in rels
    assert ("event:free-folk-nite", "FEATURES", "artist:skinny-mos") in rels
    assert ("event:free-folk-nite", "FEATURES", "artist:dolinman") in rels
    assert ("event:free-folk-nite", "IN_REGION", "region:west-bengal") in rels
    assert {n.type for n in projection.nodes} == {"event", "venue", "artist", "region"}


def test_india_city_region_parses_pincode_segment() -> None:
    addr = "ELCO Arcade, B18, Hill Rd, Bandra West, Mumbai, Maharashtra 400050"
    assert _india_city_region(addr) == ("Mumbai", "Maharashtra")


def test_district_event_from_jsonld() -> None:
    html = """
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Event","name":"Prateek Kuhad Live",
     "startDate":"2026-09-12T19:00:00.000Z",
     "location":{"@type":"Place","name":"Phoenix Marketcity","address":"Whitefield, Bengaluru, Karnataka 560048"},
     "offers":{"@type":"AggregateOffer","lowPrice":1499,"priceCurrency":"INR"},
     "performer":[{"@type":"Person","name":"Prateek Kuhad"}],
     "organizer":{"@type":"Organization","name":"District"}}
    </script>"""
    events = _jsonld_events(html)
    assert len(events) == 1
    ev = event_from_district(events[0], "https://www.district.in/events/prateek-kuhad-blr")
    assert ev.source == "district"
    assert ev.event_name == "Prateek Kuhad Live"
    assert ev.city == "Bengaluru" and ev.region == "Karnataka"
    assert ev.venue_name == "Phoenix Marketcity"
    assert ev.artists == ["Prateek Kuhad"]
    assert ev.price_min == 1499.0 and ev.currency == "INR"
    assert ev.fill_ratio is None  # District has no sold-count


def test_skillbox_event_from_details() -> None:
    data = {
        "EventId": 35536, "event_slug": "vanaghotra-the-decade-ritual",
        "event_display_name": "Vanaghotra || The Decade Ritual",
        "date_from": "2026-12-31 15:00:00", "min_price": 1999, "max_price": 3500,
        "city_name": "Goa", "venue_name": "DPedro",
        "venue_address": "Mandrem, Goa 403512, India", "status": 1,
    }
    ev = event_from_skillbox(data)
    assert ev.source == "skillbox" and ev.source_event_id == "35536"
    assert ev.event_name == "Vanaghotra || The Decade Ritual"
    assert ev.city == "Goa" and ev.venue_name == "DPedro"
    assert ev.price_min == 1999.0 and ev.currency == "INR"
    assert ev.starts_at is not None and ev.fill_ratio is None


def test_image_url_captured_across_sources() -> None:
    # Boshow: relative show_image_link -> absolute URL.
    b = event_from_boshow({
        "slug": "x", "display_name": "X", "city": "Kolkata-West Bengal-India",
        "show_image_link": "show_images/abc.jpg",
    })
    assert b.image_url == "https://www.boshow.in/show_images/abc.jpg"
    # JSON-LD (District/AllEvents/Luma/Meetup): schema.org `image` as a list.
    j = event_from_jsonld(
        {"name": "E", "image": ["https://img.example/1.jpg", "https://img.example/2.jpg"]},
        "https://www.district.in/events/e", source="district", source_event_id="e",
    )
    assert j.image_url == "https://img.example/1.jpg"
    # Skillbox: cover_image.
    s = event_from_skillbox({"event_slug": "e", "event_display_name": "E", "cover_image": "https://cdn/s.jpg"})
    assert s.image_url == "https://cdn/s.jpg"
    # absent image -> None (no observation emitted)
    assert event_from_skillbox({"event_slug": "e", "event_display_name": "E"}).image_url is None


# --- Townscript (reverse-engineered summary-page-data JSON; captured live) -----------------
_TOWNSCRIPT_SAMPLE = {
    "event": {
        "id": 61839, "name": "TIBCO BusinessWorks BW 6.X Online Training",
        "shortName": "tibco-businessworks-bw-6x-online-training-204343",
        "venueLocation": "London", "city": "London", "venueState": "England",
        "country": "United Kingdom", "startTime": "2018-02-27T23:30:00.000+0000",
        "freeEventFlag": False, "eventTopic": "BUSINESS", "soldOutFlag": True,
        "live": True, "draft": False, "spamScore": 0.00,
    },
    "user": {"name": "virtualnuggetsradha", "currencyCode": "INR"},
    "topics": [{"topicName": "Education"}, {"topicName": "Business"}],
}


def test_townscript_event_from_summary() -> None:
    ev = event_from_townscript(_TOWNSCRIPT_SAMPLE)
    assert ev.source == "townscript" and ev.source_event_id == "61839"
    assert ev.event_name == "TIBCO BusinessWorks BW 6.X Online Training"
    assert ev.city == "London" and ev.region == "England" and ev.country == "United Kingdom"
    assert ev.venue_name == "London" and ev.curator == "virtualnuggetsradha"
    assert ev.category == "Education"  # topics[0] wins over the raw eventTopic
    assert ev.currency == "INR" and ev.price_min is None  # summary API omits price
    assert ev.artists == [] and ev.fill_ratio is None
    assert ev.starts_at is not None  # proves fromisoformat handles the '+0000' offset
    assert ev.verified is True
    assert ev.event_url.endswith("/e/tibco-businessworks-bw-6x-online-training-204343")


def test_townscript_spam_score_flips_verified() -> None:
    spammy = {**_TOWNSCRIPT_SAMPLE, "event": {**_TOWNSCRIPT_SAMPLE["event"], "spamScore": 0.9}}
    assert event_from_townscript(spammy).verified is False
    draft = {**_TOWNSCRIPT_SAMPLE, "event": {**_TOWNSCRIPT_SAMPLE["event"], "draft": True}}
    assert event_from_townscript(draft).verified is False


# --- AllEvents (aggregator; schema.org JSON-LD with PostalAddress + AggregateOffer) --------
_ALLEVENTS_JSONLD = {
    "@type": "Event", "name": "The Edge Of Nutrition 2026",
    "startDate": "2026-08-08T09:00:00+05:30",
    "location": {"@type": "Place", "name": "The Chancery Pavilion", "address": {
        "@type": "PostalAddress", "streetAddress": "135, Residency Rd, Bengaluru, Karnataka 560025, India",
        "postalCode": "560025", "addressLocality": "Bangalore", "addressRegion": "KA", "addressCountry": "IN"}},
    "offers": [
        {"@type": "AggregateOffer", "priceCurrency": "INR", "lowPrice": "1299.00", "highPrice": "1899.00", "price": "1299.00"},
        {"@type": "Offer", "priceCurrency": "INR", "price": "1899.00"},
    ],
}


def test_allevents_event_from_jsonld() -> None:
    ev = event_from_jsonld(_ALLEVENTS_JSONLD, "https://allevents.in/bangalore/x-tickets/80003694098114",
                           source="allevents", source_event_id="80003694098114", country="")
    assert ev.source == "allevents" and ev.source_event_id == "80003694098114"
    assert ev.event_name == "The Edge Of Nutrition 2026"
    assert ev.city == "Bangalore" and ev.region == "KA" and ev.country == "IN"
    assert ev.venue_name == "The Chancery Pavilion"
    assert ev.price_min == 1299.0 and ev.currency == "INR"  # lowest across offers
    assert ev.is_free is False and ev.artists == [] and ev.fill_ratio is None


# --- Luma (SSR JSON-LD; VirtualLocation + free USD offer) ---------------------------------
_LUMA_JSONLD = {
    "@type": "Event", "name": "Artizen LIVE #84",
    "location": {"@type": "VirtualLocation", "name": "Online Event", "url": "https://luma.com/artizen-live-84"},
    "startDate": "2026-07-30T10:00:00.000-07:00",
    "organizer": [{"@type": "Organization", "name": "Artizen"}, {"@type": "Person", "name": "Artizen"}],
    "offers": [{"@type": "Offer", "name": "General Admission", "price": 0, "priceCurrency": "usd"}],
}


def test_luma_event_from_jsonld() -> None:
    ev = event_from_jsonld(_LUMA_JSONLD, "https://luma.com/artizen-live-84",
                           source="luma", source_event_id="artizen-live-84", country="")
    assert ev.source == "luma" and ev.event_name == "Artizen LIVE #84"
    assert ev.venue_name == "Online Event" and ev.city == "" and ev.country == ""
    assert ev.currency == "USD" and ev.price_min == 0.0 and ev.is_free is True
    assert ev.curator == "Artizen" and ev.starts_at is not None


# --- Meetup (SSR JSON-LD; null offers/performer, org name with comma) ---------------------
_MEETUP_JSONLD = {
    "@type": "Event", "name": "THE ABUNDANCE CLUB: Empower Your Life ",
    "startDate": "2026-07-29T19:30:00+01:00",
    "location": {"@type": "VirtualLocation", "url": "https://www.meetup.com/x/events/315775795/"},
    "offers": None, "performer": None,
    "organizer": {"@type": "Organization", "name": "The Law of Attraction Centre, London"},
}


def test_meetup_event_from_jsonld() -> None:
    ev = event_from_jsonld(_MEETUP_JSONLD, "https://www.meetup.com/x/events/315775795/",
                           source="meetup", source_event_id="315775795", country="")
    assert ev.source == "meetup" and ev.event_name == "THE ABUNDANCE CLUB: Empower Your Life"
    assert ev.price_min is None and ev.currency == "INR"  # null offers -> no price, default ccy
    assert ev.curator == "The Law of Attraction Centre, London"
    assert ev.venue_name == "" and ev.artists == [] and ev.fill_ratio is None


def test_jsonld_price_picks_lowest_and_uppercases_currency() -> None:
    assert _jsonld_price([{"price": "300", "priceCurrency": "inr"}, {"lowPrice": 150}]) == (150.0, "INR")
    assert _jsonld_price(None) == (None, "INR")


# --- Knowafest (static SSR HTML; og:title + state link + labeled fee/date) ----------------
_KNOWAFEST_HTML = """
<html><head>
<meta property="og:title"
 content="AVANZARE V19.0 - Hackathon Phase II, Kongu Engineering College, Hackathon, Erode" />
</head><body>
<a href="/category/Hackathon" target="_blank">Hackathon</a>
<a href="/category/Tamil_Nadu" target="_blank" >Tamil Nadu </a>
<h5>Registration Fees</h5><p>Registration Fee: &#8377;420 Per Member</p>
<span>Event Dates: 07.08.2026 &#8211; 08.08.2026</span>
</body></html>
""".replace("&#8377;", "₹")


def test_knowafest_event_from_html() -> None:
    ref = "https://www.knowafest.com/explore/events/2026/07/2904-avanzare-v19-0-hackathon-phase-ii"
    ev = event_from_knowafest(_KNOWAFEST_HTML, ref)
    assert ev.source == "knowafest"
    assert ev.event_name == "AVANZARE V19.0 - Hackathon Phase II"
    assert ev.venue_name == "Kongu Engineering College" and ev.curator == "Kongu Engineering College"
    assert ev.category == "Hackathon" and ev.city == "Erode" and ev.region == "Tamil Nadu"
    assert ev.price_min == 420.0 and ev.currency == "INR"
    assert ev.starts_at is not None and ev.starts_at.year == 2026 and ev.starts_at.month == 8
    assert ev.source_event_id == "2026/07/2904-avanzare-v19-0-hackathon-phase-ii"


# --- shared plumbing -----------------------------------------------------------------------
def test_sitemap_slugs_respects_custom_separator() -> None:
    xml = ("<urlset><url><loc>https://x.com/e/alpha-101/</loc></url>"
           "<url><loc>https://x.com/e/beta-202</loc></url>"
           "<url><loc>https://x.com/about</loc></url></urlset>")
    assert _sitemap_slugs(xml, 10, sep="/e/") == ["alpha-101", "beta-202"]


def test_maybe_gunzip_transparently_decompresses() -> None:
    import gzip as _gz
    payload = b"<urlset><url><loc>https://www.meetup.com/g/events/1/</loc></url></urlset>"
    assert _maybe_gunzip(_gz.compress(payload), "https://www.meetup.com/sw_events_1.xml.gz") == payload.decode()
    assert _maybe_gunzip(payload, "https://www.meetup.com/plain.xml") == payload.decode()


def test_get_provider_resolves_new_enum_values(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = {
        "townscript": TownscriptProvider, "allevents": AllEventsProvider,
        "luma": LumaProvider, "meetup": MeetupProvider, "knowafest": KnowafestProvider,
    }
    for value, cls in cases.items():
        monkeypatch.setattr(settings, "ticketing_provider", value)
        provider = get_provider()
        assert isinstance(provider, cls) and provider.name == value


@pytest.fixture()
def _offline_ticketing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "ticketing_provider", "mock")

    async def fake_resolve(self, **kwargs):
        etype = kwargs["entity_type"]
        slug = kwargs["display_name"].lower().replace(" ", "-")
        return {"canonical_id": f"{etype}:{slug}", "created": True}

    async def fake_projection(self, projection):
        return {"nodes": len(projection.nodes), "edges": len(projection.edges)}

    monkeypatch.setattr(
        "signal_service.routes.ticketing.EntityServiceClient.resolve", fake_resolve
    )
    monkeypatch.setattr(
        "signal_service.routes.ticketing.GraphServiceClient.upsert_projection", fake_projection
    )


def test_discover_lists_event_refs(client: TestClient, _offline_ticketing: None) -> None:
    resp = client.get("/v1/signals/ticketing/discover")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "mock"
    assert "free-folk-nite-01082026" in body["event_refs"]


def test_ingest_with_trace_is_multi_entity(client: TestClient, _offline_ticketing: None) -> None:
    stored = [{"id": f"00000000-0000-0000-0000-00000000000{i}"} for i in range(9)]
    with patch(
        "signal_service.routes.ticketing.ObservationServiceClient.append_observations",
        new_callable=AsyncMock,
        return_value=stored,
    ):
        resp = client.post(
            "/v1/signals/ticketing/events/free-folk-nite-01082026/ingest?trace=true"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fill_ratio"] == round(10 / 50, 3)
    assert [r["stage"] for r in body["trace"]] == ["ingestion", "observation", "entity", "graph"]
    # entity stage resolved an event, a venue, and multiple artists
    assert body["resolved"]["event"] == "event:free-folk-nite"
    assert body["resolved"]["venue"] == "venue:skinny-mos"
    assert len(body["resolved"]["artists"]) >= 2
    # graph stage carries the structural relationships
    graph_out = body["trace"][3]["output"]
    assert any(e["relationship"] == "OCCURS_AT" for e in graph_out["edges"])
    assert any(e["relationship"] == "FEATURES" for e in graph_out["edges"])


def test_health_reports_ticketing_provider(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ticketing_provider", "mock")
    assert client.get("/health").json()["ticketing_provider"] == "mock"


# --------------------------------------------------------------------------- Shadow Ledger (Phase 1)
async def test_fill_ratio_observation_tagged_observed_public_state() -> None:
    from signal_service.adapters.ticketing import commercial_state

    event = await MockTicketingProvider().extract("free-folk-nite-01082026")
    obs = normalize_event(event)
    fill = next(o for o in obs if o.attribute == "fill_ratio")
    # ADR-0003: displayed fill_ratio is an observed public state, never verified sell-through.
    assert fill.metadata["epistemic_status"] == "observed_public_state"
    # targeted: a non-commercial observation does not carry the tag
    name = next(o for o in obs if o.attribute == "event_name")
    assert "epistemic_status" not in name.metadata
    # the commercial-state mapper returns a Phase 1.1 structured capture
    cs = commercial_state(event)
    assert cs["values"]["fill_ratio"] == event.fill_ratio and cs["values"]["venue"] == event.venue_name
    assert cs["field_status"]["fill_ratio"] == "OBSERVED_VALUE"
    assert cs["field_status"]["availability"] == "NOT_SUPPORTED"  # Boshow doesn't expose it
    assert cs["snapshot_completeness"] == "COMPLETE"


def test_ingest_shadow_ledger_off_by_default_keeps_trace_shape(
    client: TestClient, _offline_ticketing: None
) -> None:
    # Default: shadow ledger disabled -> ingest response + trace are byte-identical to before.
    assert settings.shadow_ledger_enabled is False
    stored = [{"id": f"00000000-0000-0000-0000-00000000000{i}"} for i in range(9)]
    with patch(
        "signal_service.routes.ticketing.ObservationServiceClient.append_observations",
        new_callable=AsyncMock, return_value=stored,
    ):
        body = client.post(
            "/v1/signals/ticketing/events/free-folk-nite-01082026/ingest?trace=true"
        ).json()
    assert [r["stage"] for r in body["trace"]] == ["ingestion", "observation", "entity", "graph"]
    assert "shadow_ledger" not in body


def test_ingest_emits_shadow_stage_when_enabled(
    client: TestClient, _offline_ticketing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "shadow_ledger_enabled", True)
    fake = {
        "canonical_event_id": "event:free-folk-nite", "noop": False,
        "state": {"id": "state-1"},
        "transitions": [{"transition_type": "EVENT_FIRST_SEEN"}],
    }
    stored = [{"id": f"00000000-0000-0000-0000-00000000000{i}"} for i in range(9)]
    with patch(
        "signal_service.routes.ticketing.ObservationServiceClient.append_observations",
        new_callable=AsyncMock, return_value=stored,
    ), patch(
        "signal_service.routes.ticketing.ShadowLedgerClient.observe",
        new_callable=AsyncMock, return_value=fake,
    ):
        body = client.post(
            "/v1/signals/ticketing/events/free-folk-nite-01082026/ingest?trace=true"
        ).json()
    assert [r["stage"] for r in body["trace"]] == [
        "ingestion", "observation", "entity", "graph", "shadow_ledger",
    ]
    assert body["shadow_ledger"]["transitions"][0]["transition_type"] == "EVENT_FIRST_SEEN"
    shadow_stage = body["trace"][4]
    assert shadow_stage["output"]["transitions"] == ["EVENT_FIRST_SEEN"]


def test_ingest_survives_shadow_ledger_failure(
    client: TestClient, _offline_ticketing: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "shadow_ledger_enabled", True)
    stored = [{"id": f"00000000-0000-0000-0000-00000000000{i}"} for i in range(9)]
    with patch(
        "signal_service.routes.ticketing.ObservationServiceClient.append_observations",
        new_callable=AsyncMock, return_value=stored,
    ), patch(
        "signal_service.routes.ticketing.ShadowLedgerClient.observe",
        new_callable=AsyncMock, side_effect=RuntimeError("graph down"),
    ):
        resp = client.post("/v1/signals/ticketing/events/free-folk-nite-01082026/ingest?trace=true")
    # best-effort: ingest still succeeds; shadow stage reports it was skipped
    assert resp.status_code == 200
    body = resp.json()
    assert body["shadow_ledger"] is None
    assert body["trace"][4]["output"] == {"skipped": "shadow-ledger unreachable"}
