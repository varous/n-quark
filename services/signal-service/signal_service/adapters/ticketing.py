"""Ticketing adapter — live-event supply + demand ground truth.

Ticketing is the closest thing to commercial truth in this domain: an event's fill ratio
(tickets sold / capacity) is real demand, not a search proxy. The dominant hub (BookMyShow)
is bot-walled with no public API, so it is parked as ``partner_feed`` (needs a deal). The
regional players are far more open — **Boshow** (Kolkata-rooted grassroots platform) exposes an
unauthenticated form-encoded search API that uniquely returns ``tickets_sold`` and capacity per show.

No single canonical API exists, so the provider is pluggable (like Google Trends):
  - ``mock``     — default; seeded with real Boshow-shaped records, so tests are faithful.
  - ``boshow``   — real, public_scrape of the unauthenticated /api/search JSON.
  - ``district`` / ``skillbox`` — sitemap-driven; same interface.
  - ``townscript`` — BMS-owned DIY ticketing (SPA); extracts via the app's JSON API, which
    needs the *static, anonymous* ROLE_CLIENT token that ships in the public JS bundle.
  - ``allevents`` — event *aggregator* (schema.org JSON-LD); mainly a discovery/dedup source.
  - ``luma`` / ``meetup`` — community/grassroots platforms; sitemap + SSR schema.org JSON-LD.
  - ``knowafest`` — college-fest listings; static SSR HTML (no JSON-LD), og:title-driven.
  - ``bookmyshow`` — partner_feed scaffold; raises until a data partnership exists.

The JSON-LD providers (District, AllEvents, Luma, Meetup) all share ``event_from_jsonld``.

Every provider yields a provider-neutral ``TicketingEvent``. One event is inherently
multi-entity — an event, a venue, and a lineup of artists — so this adapter is the first to
populate *structural* graph relationships (OCCURS_AT / FEATURES / IN_REGION), and the artists
resolve by name into the same canonical entities the YouTube/Trends pipelines produce.
"""

from __future__ import annotations

import gzip
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo
from typing import Any, ClassVar, Protocol

import httpx

from signal_service.config import settings
from signal_service.schemas import NormalizedObservation

ADAPTER_VERSION = "ticketing-v1"

DISTRICT_BASE = "https://www.district.in"
SKILLBOX_BASE = "https://www.skillboxes.com"
TOWNSCRIPT_BASE = "https://www.townscript.com"
ALLEVENTS_BASE = "https://allevents.in"
LUMA_BASE = "https://luma.com"
LUMA_SITEMAP_INDEX = "https://sitemap.luma.com/sitemap.xml"
MEETUP_BASE = "https://www.meetup.com"
MEETUP_EVENTS_SITEMAP = "https://www.meetup.com/events-index-sitemap.xml"
KNOWAFEST_BASE = "https://www.knowafest.com"
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}

# Boshow separates performers in a lineup string with a bullet.
_LINEUP_SEP = re.compile(r"\s*[•|]\s*")


class EventNotFound(Exception):
    """Raised when the source request SUCCEEDED but the event record is genuinely absent.

    Distinct from a failed request (network/timeout/parse) — the scheduler maps this to an
    authoritative absence, never a source failure.
    """


@dataclass
class TicketingEvent:
    """Provider-neutral normalized event — what every ticketing provider must return."""

    source: str
    source_event_id: str
    event_slug: str
    event_name: str
    event_url: str
    city: str
    region: str
    country: str
    venue_name: str
    artists: list[str]
    category: str
    language: str
    currency: str
    price_min: float | None
    is_free: bool
    starts_at: datetime | None
    capacity: int | None
    tickets_sold: int | None
    verified: bool
    artist_source_id: str | None = None
    curator: str | None = None
    image_url: str | None = None
    source_city_id: str | None = None  # Phase 4C.1 — stable source city id, when the source exposes one
    ends_at: datetime | None = None
    event_date: str | None = None
    provider_lifecycle: str | None = None
    source_start_value: str | None = None
    source_end_value: str | None = None
    source_time_precision: str = "UNKNOWN"
    source_timezone: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def fill_ratio(self) -> float | None:
        """Tickets sold / capacity — the demand ground truth. None if capacity unknown."""
        if not self.capacity or self.tickets_sold is None:
            return None
        return round(min(self.tickets_sold / self.capacity, 1.0), 3)


# ------------------------------------------------------------------- parsing helpers
def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def split_location(city_field: str) -> tuple[str, str, str]:
    """Boshow encodes location as 'City-State-Country' (e.g. 'Kolkata-West Bengal-India')."""
    parts = [p.strip() for p in (city_field or "").split("-") if p.strip()]
    city = parts[0] if parts else ""
    region = parts[1] if len(parts) > 1 else ""
    country = parts[2] if len(parts) > 2 else "India"
    return city, region, country


def split_lineup(name_of_artist: str, member_name: str | None = None) -> list[str]:
    """Split a bullet-separated lineup into distinct performer names (order-preserving)."""
    raw = _LINEUP_SEP.split(name_of_artist or "")
    names: list[str] = []
    for name in raw:
        cleaned = name.strip()
        # Drop generic prefixes Boshow sometimes uses ("Live at X", "Live at Skinny Mos").
        if not cleaned or cleaned.lower().startswith("live at"):
            continue
        if cleaned not in names:
            names.append(cleaned)
    if not names and member_name:
        names = [member_name.strip()]
    return names


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Python 3.11+ fromisoformat handles the trailing 'Z' and fractional seconds.
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def event_from_boshow(item: dict[str, Any], *, fetched_at: datetime | None = None) -> TicketingEvent:
    """Map one Boshow /api/search record to the canonical TicketingEvent."""
    show_ids = item.get("show_id") or []
    source_event_id = show_ids[0] if isinstance(show_ids, list) and show_ids else (item.get("slug") or "")
    city, region, country = split_location(item.get("city", ""))
    price = item.get("default_price", item.get("price"))
    slug = item.get("slug") or _slug(item.get("display_name") or item.get("show_name") or "")
    return TicketingEvent(
        source="boshow",
        source_event_id=str(source_event_id),
        event_slug=slug,
        event_name=item.get("display_name") or item.get("show_name") or "",
        event_url=item.get("share_url") or f"https://www.boshow.in/shows.html?slug={slug}",
        city=city,
        region=region,
        country=country,
        venue_name=(item.get("location") or "").strip(),
        artists=split_lineup(item.get("name_of_artist", ""), item.get("member_name")),
        artist_source_id=item.get("artist_id"),
        curator=item.get("curator_name"),
        category=item.get("show_type") or "Event",
        language=item.get("language") or "",
        currency=item.get("currency") or item.get("default_curr") or "INR",
        price_min=float(price) if isinstance(price, (int, float)) else None,
        is_free=bool(item.get("free_show")),
        starts_at=_parse_dt(item.get("real_show_date")),
        capacity=item.get("gc") if isinstance(item.get("gc"), int) else None,
        tickets_sold=item.get("tickets_sold") if isinstance(item.get("tickets_sold"), int) else None,
        verified=bool(item.get("verified")),
        image_url=(f"https://www.boshow.in/{item['show_image_link']}" if item.get("show_image_link") else None),
        fetched_at=fetched_at or datetime.now(UTC),
    )


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _india_city_region(address: str) -> tuple[str, str]:
    """Best-effort city/state from an Indian address string: '..., City, State PIN'."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    for i, seg in enumerate(parts):
        m = re.search(r"\b(\d{6})\b", seg)  # the segment with the 6-digit PIN is 'State PIN'
        if m:
            region = seg.replace(m.group(1), "").strip()
            city = parts[i - 1] if i > 0 else ""
            return city, region
    return (parts[-2] if len(parts) >= 2 else ""), (parts[-1] if parts else "")


def _jsonld_events(html: str) -> list[dict[str, Any]]:
    """Extract schema.org Event nodes from a page's JSON-LD (handles @graph + arrays)."""
    events: list[dict[str, Any]] = []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        nodes = parsed if isinstance(parsed, list) else [parsed]
        expanded: list[Any] = []
        for node in nodes:
            if isinstance(node, dict) and "@graph" in node:
                expanded.extend(node["@graph"])
            else:
                expanded.append(node)
        events.extend(n for n in expanded if isinstance(n, dict) and n.get("@type") == "Event")
    return events


def _jsonld_price(offers: Any) -> tuple[float | None, str]:
    """Lowest price + currency from a schema.org ``offers`` value (scalar, list, or AggregateOffer).

    Handles string prices (``"1299.00"``) and both ``lowPrice`` (AggregateOffer) and ``price``.
    """
    items = offers if isinstance(offers, list) else ([offers] if isinstance(offers, dict) else [])
    prices: list[float] = []
    currency = ""
    for offer in items:
        if not isinstance(offer, dict):
            continue
        currency = currency or (offer.get("priceCurrency") or "")
        price = _to_float(offer.get("lowPrice"))
        if price is None:
            price = _to_float(offer.get("price"))
        if price is not None:
            prices.append(price)
    return (min(prices) if prices else None), (currency or "INR").upper()


def _jsonld_image(value: Any) -> str | None:
    """First usable image URL from a schema.org ``image`` (string, list, or ImageObject)."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):
        for item in value:
            url = _jsonld_image(item)
            if url:
                return url
        return None
    if isinstance(value, dict):
        return value.get("url") or None
    return None


def event_from_jsonld(
    node: dict[str, Any], url: str, *, source: str, source_event_id: str,
    country: str = "India", fetched_at: datetime | None = None,
) -> TicketingEvent:
    """Map a schema.org Event (JSON-LD) node to the canonical TicketingEvent.

    Shared by every SSR/JSON-LD provider (District, AllEvents, Luma, Meetup). Handles a
    ``Place`` or ``VirtualLocation``, a ``PostalAddress`` dict or a flat address string
    (Indian PIN heuristic), list-or-scalar offers/performers/organizer, and string prices.
    ``country`` is the fallback when the address carries no ``addressCountry``.
    """
    loc = node.get("location") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    venue = loc.get("name", "") if isinstance(loc, dict) else ""
    addr = loc.get("address", "") if isinstance(loc, dict) else ""
    if isinstance(addr, dict):
        city, region = addr.get("addressLocality", ""), addr.get("addressRegion", "")
        addr_country = addr.get("addressCountry")
        if isinstance(addr_country, dict):
            addr_country = addr_country.get("name")
        country = addr_country or country
    else:
        city, region = _india_city_region(addr)

    price, currency = _jsonld_price(node.get("offers"))
    perf = node.get("performer") or []
    if isinstance(perf, dict):
        perf = [perf]
    artists = [p["name"].strip() for p in perf if isinstance(p, dict) and p.get("name")]
    org = node.get("organizer") or {}
    if isinstance(org, list):
        org = org[0] if org else {}
    keywords = node.get("keywords")
    raw_start = node.get("startDate") if isinstance(node.get("startDate"), str) else None
    raw_end = node.get("endDate") if isinstance(node.get("endDate"), str) else None
    date_only = bool(raw_start and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_start.strip()))
    start = None if date_only else _parse_dt(raw_start)
    end = _parse_dt(raw_end)
    aware = bool(start and start.tzinfo is not None)
    # District/other India JSON-LD pages describe local Indian event time. A date-only value remains
    # a date (not midnight); a naive clock stays uncertainty-bearing rather than acquiring server UTC.
    source_timezone = "Asia/Kolkata" if country.strip().lower() == "india" else None
    precision = "DATE_ONLY" if date_only else ("START_END_DATETIME" if aware and end and end.tzinfo else ("START_DATETIME_ONLY" if aware else "UNKNOWN"))
    return TicketingEvent(
        source=source, source_event_id=source_event_id, event_slug=source_event_id,
        event_name=(node.get("name") or "").strip(), event_url=url,
        city=(city or "").strip(), region=(region or "").strip(), country=(country or "").strip(),
        venue_name=(venue or "").strip(), artists=artists,
        curator=org.get("name") if isinstance(org, dict) else None,
        category=(keywords.split(",")[0].strip() if isinstance(keywords, str) and keywords else "Event"),
        language="", currency=currency, price_min=price,
        is_free=(price == 0.0), starts_at=start,
        capacity=None, tickets_sold=None, verified=True,
        image_url=_jsonld_image(node.get("image")),
        ends_at=end if end and end.tzinfo is not None else None,
        event_date=raw_start if date_only else None,
        provider_lifecycle=node.get("eventStatus") if isinstance(node.get("eventStatus"), str) else None,
        source_start_value=raw_start, source_end_value=raw_end,
        source_time_precision=precision, source_timezone=source_timezone,
        fetched_at=fetched_at or datetime.now(UTC),
    )


def event_from_district(node: dict[str, Any], url: str, *, fetched_at: datetime | None = None) -> TicketingEvent:
    """Map a District schema.org Event (JSON-LD) to the canonical TicketingEvent."""
    slug = url.rstrip("/").split("/events/")[-1]
    return event_from_jsonld(node, url, source="district", source_event_id=slug, country="India", fetched_at=fetched_at)


def event_from_skillbox(data: dict[str, Any], *, fetched_at: datetime | None = None) -> TicketingEvent:
    """Map a Skillbox event-details record to the canonical TicketingEvent."""
    slug = data.get("event_slug", "")
    starts = data.get("date_from")
    starts_dt = _parse_dt(starts.replace(" ", "T")) if isinstance(starts, str) else None
    price = _to_float(data.get("min_price"))
    city = (data.get("city_name") or "").strip()
    return TicketingEvent(
        source="skillbox", source_event_id=str(data.get("EventId") or slug), event_slug=slug,
        event_name=(data.get("event_display_name") or "").strip(),
        event_url=f"{SKILLBOX_BASE}/events/{slug}",
        city=city, region=city, country="India",  # Skillbox city_name doubles as the state (e.g. Goa)
        venue_name=(data.get("venue_name") or "").strip(), artists=[], curator=None,
        category="Event", language="", currency="INR", price_min=price,
        is_free=(price == 0.0), starts_at=starts_dt, capacity=None, tickets_sold=None,
        verified=bool(data.get("status")), image_url=(data.get("cover_image") or None),
        source_city_id=(str(data["city_id"]) if data.get("city_id") else None),
        fetched_at=fetched_at or datetime.now(UTC),
    )


def event_from_townscript(payload: dict[str, Any], *, fetched_at: datetime | None = None) -> TicketingEvent:
    """Map a Townscript ``summary-page-data`` record (its inner ``data`` object) to TicketingEvent.

    The summary endpoint omits ticket price (only a ``freeEventFlag``), so ``price_min`` is None
    like the other sitemap providers. ``verified`` folds in Townscript's own spam signal — the
    platform is DIY, so it carries listing spam (fake pharma/"training" events) with a spamScore.
    """
    event = payload.get("event") or {}
    user = payload.get("user") or {}
    topics = payload.get("topics") or []
    slug = event.get("shortName") or ""
    topic = topics[0].get("topicName") if topics and isinstance(topics[0], dict) else None
    category = topic or (event.get("eventTopic") or "Event").replace("_", " ").title()
    return TicketingEvent(
        source="townscript",
        source_event_id=str(event.get("id") or slug),
        event_slug=slug,
        event_name=(event.get("name") or "").strip(),
        event_url=f"{TOWNSCRIPT_BASE}/e/{slug}",
        city=(event.get("city") or "").strip(),
        region=(event.get("venueState") or "").strip(),
        country=(event.get("country") or "").strip(),
        venue_name=(event.get("venueLocation") or "").strip(),
        artists=[],
        curator=(user.get("name") or event.get("organizerName") or None),
        category=category,
        language="",
        currency=(user.get("currencyCode") or "INR").upper(),
        price_min=None,
        is_free=bool(event.get("freeEventFlag")),
        starts_at=_parse_dt(event.get("startTime")),
        capacity=None,
        tickets_sold=None,
        verified=bool(event.get("live") and not event.get("draft") and float(event.get("spamScore") or 0) == 0),
        image_url=(event.get("absoluteBannerImageUrl") or event.get("absoluteMobileImageUrl") or None),
        fetched_at=fetched_at or datetime.now(UTC),
    )


# Indian states/UTs — Knowafest tags each fest with its state via a /category/<State> link,
# which is how we recover the region from an otherwise unstructured college-fest page.
_INDIA_STATES = frozenset({
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh",
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan",
    "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Delhi", "Jammu and Kashmir", "Ladakh", "Puducherry", "Chandigarh",
    "Andaman and Nicobar Islands", "Dadra and Nagar Haveli", "Daman and Diu", "Lakshadweep",
})


def _knowafest_region(html: str) -> str:
    """Recover the state from the first ``/category/<State>`` link whose text is an Indian state."""
    for text in re.findall(r'/category/[A-Za-z_]+"[^>]*>\s*([^<]+?)\s*</a>', html):
        cleaned = text.strip()
        if cleaned in _INDIA_STATES:
            return cleaned
    return ""


def event_from_knowafest(html: str, event_ref: str, *, fetched_at: datetime | None = None) -> TicketingEvent:
    """Parse a Knowafest college-fest page (static SSR HTML, no JSON-LD) into a TicketingEvent.

    The stable signal is ``og:title`` = ``"Name, College, Category, City"``. State, fee, and
    date are best-effort from labeled HTML; the value here is long-tail student-event discovery,
    not price/demand truth.
    """
    og = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    title = (og.group(1) if og else "").strip()
    if not title:
        fallback = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
        title = (fallback.group(1) if fallback else "").strip()
    parts = [p.strip() for p in title.split(",") if p.strip()]
    city = parts[-1] if parts else ""
    category = parts[-2] if len(parts) >= 2 else "Fest"
    college = parts[-3] if len(parts) >= 3 else ""
    name = ", ".join(parts[:-3]).strip() if len(parts) > 3 else (parts[0] if parts else "")

    price = None
    fee = re.search(r"Registration Fee[s]?:?\s*(?:<[^>]+>\s*)*₹\s*([\d,]+)", html)
    if fee:
        price = _to_float(fee.group(1).replace(",", ""))

    starts = None
    dates = re.search(r"Event Dates?:\s*(\d{2})\.(\d{2})\.(\d{4})", html)
    if dates:
        day, month, year = dates.groups()
        starts = _parse_dt(f"{year}-{month}-{day}T00:00:00")

    img = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    slug = event_ref.rstrip("/").split("/explore/events/")[-1]
    return TicketingEvent(
        source="knowafest", source_event_id=slug, event_slug=slug,
        event_name=name, event_url=f"{KNOWAFEST_BASE}/explore/events/{slug}",
        city=city, region=_knowafest_region(html), country="India",
        venue_name=college, artists=[], curator=college or None,
        category=category or "Fest", language="", currency="INR",
        price_min=price, is_free=bool(price is not None and price == 0.0),
        starts_at=starts, capacity=None, tickets_sold=None,
        verified=bool(name), image_url=(img.group(1) if img else None),
        fetched_at=fetched_at or datetime.now(UTC),
    )


# ------------------------------------------------------------------- providers
class TicketingProvider(Protocol):
    name: str

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]: ...

    async def extract(self, event_ref: str) -> TicketingEvent: ...


# Real Boshow records (captured live) so the mock is faithful to the true API shape.
_MOCK_BOSHOW: dict[str, dict[str, Any]] = {
    "jamsteady-with-cherry-mrong-31072026": {
        "display_name": "JamSteady with Cherry Mrong", "show_type": "Music",
        "name_of_artist": "Live at Skinny Mos", "member_name": "Skinny Mos",
        "artist_id": "cc460b7e-0193-45e5-8273-55c06a819fff",
        "location": "Skinny Mos", "city": "Kolkata-West Bengal-India",
        "currency": "INR", "default_price": 499, "language": "English", "free_show": 0,
        "real_show_date": "2026-07-31T20:00:00.000Z", "gc": 57, "tickets_sold": 3, "verified": 1,
        "show_id": ["619b9dbb-b708-439a-b756-5dcacd325884"],
        "slug": "jamsteady-with-cherry-mrong-31072026",
        "share_url": "https://www.boshow.in/api/shows/share/jamsteady-with-cherry-mrong-31072026",
        "show_image_link": "show_images/163f830d-6367-4bc0-9071-d531edd2d6ae.jpg",
    },
    "free-folk-nite-01082026": {
        "display_name": "Free Folk Nite", "show_type": "Music",
        "name_of_artist": "Skinny Mos • Dolinman • Gaboo • Rajoshi • Pilu • Manas",
        "member_name": "Skinny Mos", "artist_id": "cc460b7e-0193-45e5-8273-55c06a819fff",
        "location": "Skinny Mos", "city": "Kolkata-West Bengal-India",
        "currency": "INR", "default_price": 599, "language": "Bengali,English", "free_show": 0,
        "real_show_date": "2026-08-01T20:00:00.000Z", "gc": 50, "tickets_sold": 10, "verified": 1,
        "show_id": ["a7ed0638-ef5e-4f98-801b-ad46e3a75a6d"], "slug": "free-folk-nite-01082026",
        "share_url": "https://www.boshow.in/api/shows/share/free-folk-nite-01082026",
        "show_image_link": "show_images/8584b00b-c274-4913-bcb8-222461f0a844.jpg",
    },
    "atsp-viii-cotton-stainers-31072026": {
        "display_name": "ATSP VIII - Be.long.ing - Cotton Stainers by Gram Art Project",
        "show_type": "Performance Art",
        "name_of_artist": "At the Still Point by Artsforward",
        "member_name": "At the Still Point by Artsforward",
        "artist_id": "4bc26163-32dc-4f7c-bf3b-7da43e88c6de",
        "location": "The Urban Theatre Project", "city": "Kolkata-West Bengal-India",
        "currency": "INR", "default_price": 600, "language": "English", "free_show": 0,
        "real_show_date": "2026-07-31T19:30:00.000Z", "gc": 49, "tickets_sold": 31, "verified": 0,
        "show_id": ["256f6c6e-0549-4425-9b2e-12dd6c4c9ff7"],
        "slug": "atsp-viii-cotton-stainers-31072026",
        "share_url": "https://www.boshow.in/api/shows/share/atsp-viii-cotton-stainers-31072026",
        "show_image_link": "show_images/bedeba8c-6916-4536-96a4-789f6b221071.jpg",
    },
}


class MockTicketingProvider:
    name = "mock"

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        return list(_MOCK_BOSHOW.keys())[:limit]

    async def extract(self, event_ref: str) -> TicketingEvent:
        item = _MOCK_BOSHOW.get(event_ref)
        if item is None:
            raise EventNotFound(f"unknown mock event: {event_ref}")
        return event_from_boshow(item)


class BoshowProvider:
    """public_scrape of Boshow's unauthenticated /api/search (no per-show JSON endpoint).

    Boshow is an Apache Cordova hybrid app served on web. The API is not JSON — it is
    ``application/x-www-form-urlencoded`` and requires ``X-Requested-With: XMLHttpRequest``;
    that (not TLS/cookies) is why a JSON POST hangs. ``token`` is empty, so no auth is needed.
    The contract is quirky: ``offset`` behaves as a *result count* (not a skip), ``dateFrom``
    is a floor for upcoming events (``dateTo`` must equal it), and ``search`` filters by name.
    Contract captured once with a headless browser; the live provider is plain httpx.
    """

    name = "boshow"
    _UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
    # Full field set the app sends; token is empty (no auth). offset is really a max-count.
    _BASE_FORM: ClassVar[dict[str, Any]] = {
        "capacity": 0, "currency": "INR", "displayType": "shows", "genre": "",
        "guests": 1, "languages": "", "member_id": "", "online": -1, "priceRange": -1,
        "ratingRange": 60, "showType": "", "sort": "show_date", "space_filters": "",
        "token": "", "verified": 0, "limit": 0,
    }

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._UA,
            "X-Requested-With": "XMLHttpRequest",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "origin": "https://www.boshow.in",
            "referer": "https://www.boshow.in/indexall.html",
        }

    async def _search(
        self, *, term: str = "", count: int = 40, location: str = "Anywhere"
    ) -> list[dict[str, Any]]:
        # dateFrom is a floor for upcoming events; UTC (behind IST) never skips today's events.
        today = datetime.now(UTC).strftime("%Y/%m/%d")
        form = {
            **self._BASE_FORM,
            "dateFrom": today, "dateTo": today, "offset": max(count, 1),
            "search": term, "location": location or "Anywhere",
        }
        url = f"{settings.boshow_api_base.rstrip('/')}/search"
        async with httpx.AsyncClient(timeout=20.0, headers=self._headers()) as client:
            resp = await client.post(url, data=form)
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        # city, when given, filters the API's `location`; otherwise list everything upcoming.
        results = await self._search(count=max(limit, 10), location=city or "Anywhere")
        return [r.get("slug") for r in results if r.get("slug")][:limit]

    async def extract(self, event_ref: str) -> TicketingEvent:
        # `search` filters by name, so derive a term from the slug (minus its trailing date),
        # then match the exact slug. Fall back to a broad listing if the term is too narrow.
        term = " ".join(w for w in event_ref.split("-")[:4] if not w.isdigit())
        for items in (await self._search(term=term, count=60), await self._search(count=120)):
            for item in items:
                if item.get("slug") == event_ref:
                    return event_from_boshow(item)
        # The search succeeded but the slug is no longer listed -> authoritative absence.
        raise EventNotFound(f"boshow: event not found for ref {event_ref!r}")


def _sitemap_locs(xml: str) -> list[str]:
    return re.findall(r"<loc>([^<]+)</loc>", xml)


def _sitemap_slugs(xml: str, limit: int, sep: str = "/events/") -> list[str]:
    slugs = [loc.rstrip("/").split(sep)[-1] for loc in _sitemap_locs(xml) if sep in loc]
    return slugs[:limit]


_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _district_ranked_slugs(xml: str, limit: int, *, today: date | None = None) -> list[str]:
    """Prefer current dated inventory across the whole sitemap; never arbitrary first-N.

    Slug dates are discovery hints only and never become Event evidence. Unknown-date pages remain
    eligible behind current/future hints, ordered by sitemap freshness. Clearly historical hints are
    omitted; extraction remains the authority for the actual schedule.
    """
    today = today or datetime.now(ZoneInfo("Asia/Kolkata")).date()
    entries = re.findall(r"<url>.*?<loc>([^<]+)</loc>.*?(?:<lastmod>([^<]+)</lastmod>)?.*?</url>", xml, re.DOTALL)
    ranked: list[tuple[int, object, str]] = []
    for loc, lastmod in entries:
        if "/events/" not in loc:
            continue
        slug = loc.rstrip("/").split("/events/")[-1]
        hint = None
        for match in re.finditer(r"(?:^|-)(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(\d{1,2})-(20\d{2})(?:-|$)", slug):
            try:
                hint = date(int(match.group(3)), _MONTHS[match.group(1)], int(match.group(2)))
            except ValueError:
                pass
        if hint and hint < today:
            continue
        ranked.append((0 if hint else 1, hint or (lastmod or ""), slug))
    ranked.sort(key=lambda row: (row[0], row[1] if row[0] == 0 else "", row[2]), reverse=False)
    # Unknown-date entries use newest lastmod first without disturbing dated current inventory.
    current = [r[2] for r in ranked if r[0] == 0]
    unknown = [r[2] for r in sorted((r for r in ranked if r[0] == 1), key=lambda r: (r[1], r[2]), reverse=True)]
    return (current + unknown)[:limit]


def _maybe_gunzip(content: bytes, url: str) -> str:
    """Decode a sitemap body, transparently gunzipping ``.xml.gz`` children (Meetup ships these)."""
    if url.endswith(".gz") or content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    return content.decode("utf-8", "replace")


class DistrictProvider:
    """public_scrape of District (Zomato). robots-allowed; discovery via the events sitemap,
    extraction from each page's schema.org Event JSON-LD. Plain httpx (SSR pages)."""

    name = "district"

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        url = f"{DISTRICT_BASE}/events/search-sitemap/event-detail-pages.xml"
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            return _district_ranked_slugs(r.text, limit)

    async def extract(self, event_ref: str) -> TicketingEvent:
        url = event_ref if event_ref.startswith("http") else f"{DISTRICT_BASE}/events/{event_ref}"
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            events = _jsonld_events(r.text)
        if not events:
            # Reachable page with no Event JSON-LD -> treat as record absent (authoritative).
            raise EventNotFound(f"district: no Event JSON-LD at {url}")
        return event_from_district(events[0], url)


class SkillboxProvider:
    """public_scrape of Skillbox. robots-allowed; discovery via the event sitemap, extraction
    via the JSON API POST /servers/v3/api/event-new/event-details {slug}. Plain httpx."""

    name = "skillbox"

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        url = f"{SKILLBOX_BASE}/media/sitemap/sitemap-event.xml"
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            return _sitemap_slugs(r.text, limit)

    async def extract(self, event_ref: str) -> TicketingEvent:
        url = f"{SKILLBOX_BASE}/servers/v3/api/event-new/event-details"
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS) as c:
            r = await c.post(url, json={"slug": event_ref})
            r.raise_for_status()
            payload = r.json()
        if not payload.get("success") or not payload.get("data"):
            # Source reachable, record genuinely absent -> authoritative absence (not a parse error).
            raise EventNotFound(f"skillbox: event not found for slug {event_ref!r}")
        return event_from_skillbox(payload["data"])


class TownscriptProvider:
    """public_scrape of Townscript (BMS-owned DIY ticketing). robots.txt is wide-open for ``*``.

    The event page is an Angular SPA (server HTML is just a loader shell), so extraction uses the
    app's own JSON API ``/api/eventdata/summary-page-data?eventCode=<slug>``. That endpoint 401s
    unless the request carries an ``Authorization`` header holding Townscript's *static, anonymous*
    ROLE_CLIENT token (``sub: api@townscript.com``) — a public API key the app ships in cleartext
    inside its JS bundle, identical for every logged-out visitor. The provider reads that public
    token from the bundle and replays it: no user login, no per-session credential, robots
    respected. Discovery is the open ``upcoming-event-pages`` sitemap. Plain httpx.
    """

    name = "townscript"
    _token: ClassVar[str | None] = None  # cached public client token (per process)

    async def _client_token(self, client: httpx.AsyncClient) -> str:
        if TownscriptProvider._token:
            return TownscriptProvider._token
        shell = (await client.get(f"{TOWNSCRIPT_BASE}/")).text
        match = re.search(r"(main-es2015\.[0-9a-f]+\.js)", shell) or re.search(r"(main[.\-][0-9a-f]+\.js)", shell)
        if not match:
            raise ValueError("townscript: could not locate app bundle for client token")
        bundle = (await client.get(f"{TOWNSCRIPT_BASE}/{match.group(1)}")).text
        token = re.search(r'getToken\(\)\{return"([A-Za-z0-9_.\-]+)"', bundle)
        if not token:
            raise ValueError("townscript: anonymous client token not found in bundle")
        TownscriptProvider._token = token.group(1)
        return TownscriptProvider._token

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        url = f"{TOWNSCRIPT_BASE}/sitemap/upcoming-event-pages.xml"
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            return _sitemap_slugs(r.text, limit, sep="/e/")

    async def extract(self, event_ref: str) -> TicketingEvent:
        slug = event_ref.rstrip("/").split("/e/")[-1] if "/e/" in event_ref else event_ref
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            token = await self._client_token(c)
            r = await c.get(
                f"{TOWNSCRIPT_BASE}/api/eventdata/summary-page-data",
                params={"eventCode": slug},
                headers={"Authorization": token, "X-Requested-With": "XMLHttpRequest"},
            )
            r.raise_for_status()
            outer = r.json()
        raw = outer.get("data")
        if not raw:
            raise ValueError(f"townscript: no data for eventCode {slug!r}")
        payload = json.loads(raw) if isinstance(raw, str) else raw
        return event_from_townscript(payload)


class AllEventsProvider:
    """public_scrape of AllEvents.in — an event *aggregator*, so primarily a discovery/dedup source.

    robots.txt sets ``Crawl-delay: 10`` and respects ClaudeBot; each discover/extract makes a single
    request, so we stay well within it. Discovery pulls event links off a city page; extraction reads
    each event page's schema.org Event JSON-LD (shared ``event_from_jsonld``). Plain httpx.
    """

    name = "allevents"
    _EVENT_HREF = re.compile(r'https://allevents\.in/[^/"\s]+/[^"\s]+?-tickets/\d+')

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        # AllEvents city slugs are quirky (pune-in, bhopal, ...); pass the caller's city through,
        # defaulting to the global "online" listing when none is given.
        slug = (city or "online").strip().lower().replace(" ", "-")
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            r = await c.get(f"{ALLEVENTS_BASE}/{slug}")
            r.raise_for_status()
            return list(dict.fromkeys(self._EVENT_HREF.findall(r.text)))[:limit]

    async def extract(self, event_ref: str) -> TicketingEvent:
        url = event_ref if event_ref.startswith("http") else f"{ALLEVENTS_BASE}/{event_ref.lstrip('/')}"
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            events = _jsonld_events(r.text)
        if not events:
            raise ValueError(f"allevents: no Event JSON-LD at {url}")
        eid = url.rstrip("/").split("-tickets/")[-1]
        return event_from_jsonld(events[0], url, source="allevents", source_event_id=eid, country="")


class LumaProvider:
    """public_scrape of Luma (lu.ma -> luma.com). Server HTML embeds schema.org Event JSON-LD, so
    plain httpx suffices; discovery walks the sitemap index (flat ``luma.com/<slug>`` URLs). Global,
    community/grassroots events (often virtual meetups)."""

    name = "luma"

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            idx = await c.get(LUMA_SITEMAP_INDEX)
            idx.raise_for_status()
            children = _sitemap_locs(idx.text)
            if not children:
                return []
            child = await c.get(children[0])
            child.raise_for_status()
            return [loc.rstrip("/").rsplit("/", 1)[-1] for loc in _sitemap_locs(child.text)][:limit]

    async def extract(self, event_ref: str) -> TicketingEvent:
        slug = event_ref.rstrip("/").rsplit("/", 1)[-1]
        url = f"{LUMA_BASE}/{slug}"
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            events = _jsonld_events(r.text)
        if not events:
            raise ValueError(f"luma: no Event JSON-LD at {url}")
        return event_from_jsonld(events[0], url, source="luma", source_event_id=slug, country="")


class MeetupProvider:
    """public_scrape of Meetup. Event detail pages are robots-allowed and SSR schema.org Event
    JSON-LD; discovery walks the events sitemap index (children are gzipped ``.xml.gz``). The
    GraphQL/``/api`` paths are robots-disallowed, so this never touches them. Plain httpx."""

    name = "meetup"

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        async with httpx.AsyncClient(timeout=45.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            idx = await c.get(MEETUP_EVENTS_SITEMAP)
            idx.raise_for_status()
            children = _sitemap_locs(idx.text)
            if not children:
                return []
            raw = await c.get(children[0])
            raw.raise_for_status()
            xml = _maybe_gunzip(raw.content, children[0])
            return [u for u in _sitemap_locs(xml) if "/events/" in u][:limit]

    async def extract(self, event_ref: str) -> TicketingEvent:
        url = event_ref if event_ref.startswith("http") else f"{MEETUP_BASE}/{event_ref.lstrip('/')}"
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            events = _jsonld_events(r.text)
        if not events:
            raise ValueError(f"meetup: no Event JSON-LD at {url}")
        eid = url.rstrip("/").split("/events/")[-1] if "/events/" in url else url.rstrip("/").rsplit("/", 1)[-1]
        return event_from_jsonld(events[0], url, source="meetup", source_event_id=eid, country="")


class KnowafestProvider:
    """public_scrape of Knowafest (college-fest listings). robots.txt is wide-open with a single
    sitemap. Pages are static SSR HTML (no JSON-LD), so extraction parses the stable og:title plus
    labeled fee/date fields (``event_from_knowafest``). Plain httpx."""

    name = "knowafest"

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            r = await c.get(f"{KNOWAFEST_BASE}/sitemap.xml")
            r.raise_for_status()
            return _sitemap_slugs(r.text, limit, sep="/explore/events/")

    async def extract(self, event_ref: str) -> TicketingEvent:
        slug = event_ref.rstrip("/").split("/explore/events/")[-1]
        url = f"{KNOWAFEST_BASE}/explore/events/{slug}"
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            html = r.text
        return event_from_knowafest(html, event_ref)


class PartnerFeedProvider:
    """Placeholder for platforms that are not compliantly scrapable (e.g. BookMyShow).

    BMS is bot-walled with no public API; ingesting it requires a data partnership, which the
    compliance envelope models as partner_feed + consent_source. Until that exists, this raises
    rather than pretending, and never attempts to evade access controls.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        raise RuntimeError(f"{self.name} requires a partner feed (no compliant public access)")

    async def extract(self, event_ref: str) -> TicketingEvent:
        raise RuntimeError(f"{self.name} requires a partner feed (no compliant public access)")


def get_provider(name: str | None = None) -> TicketingProvider:
    # Per-request override lets the scheduler capture multiple sources through one ingest route
    # (Phase 3); falls back to the globally configured provider.
    provider = (name or settings.ticketing_provider).lower()
    if provider == "boshow":
        return BoshowProvider()
    if provider == "district":
        return DistrictProvider()
    if provider == "skillbox":
        return SkillboxProvider()
    if provider == "townscript":
        return TownscriptProvider()
    if provider == "allevents":
        return AllEventsProvider()
    if provider == "luma":
        return LumaProvider()
    if provider == "meetup":
        return MeetupProvider()
    if provider == "knowafest":
        return KnowafestProvider()
    if provider in ("bookmyshow", "bms"):
        return PartnerFeedProvider("bookmyshow")
    return MockTicketingProvider()


# ------------------------------------------------------------------- entity handles
def event_handle(event: TicketingEvent) -> str:
    return f"{event.source}:show:{event.source_event_id}"


def venue_handle(event: TicketingEvent) -> str:
    return f"{event.source}:venue:{_slug(event.venue_name)}"


def artist_handle(event: TicketingEvent, name: str, index: int) -> str:
    # Prefer the platform's artist id for the primary performer; slug the rest.
    if index == 0 and event.artist_source_id:
        return f"{event.source}:artist:{event.artist_source_id}"
    return f"{event.source}:artist:{_slug(name)}"


# ------------------------------------------------------------------- normalization
def _provenance(event: TicketingEvent) -> dict[str, Any]:
    return {
        "acquisition_method": "public_scrape",
        "legal_basis": "legitimate_interest",
        "data_subject_type": "entity",
        "contains_pii": False,
        "adapter_version": ADAPTER_VERSION,
        "collected_at": event.fetched_at.isoformat(),
        "source_url": event.event_url,
        "logged_out": True,
        "robots_respected": True,
    }


def normalize_event(event: TicketingEvent) -> list[NormalizedObservation]:
    """Turn one event into append-only observations, keyed on type-neutral source handles.

    Emits event supply/demand signals (the fill_ratio is the ground-truth demand signal),
    plus relationship observations (lineup, venue, region) the graph stage turns into edges.
    """
    handle = event_handle(event)
    when = event.fetched_at
    meta = {"adapter": ADAPTER_VERSION, "source": event.source, "provenance": _provenance(event)}

    def obs(
        attribute: str, value: Any, confidence: float,
        evidence: dict[str, Any] | None = None, epistemic_status: str | None = None,
    ) -> NormalizedObservation:
        # epistemic_status qualifies how the value should be read (ADR-0003): public ticket state is
        # an observed_public_state, never verified sell-through. Carried in metadata (backward-compatible).
        observation_meta = {**meta, "epistemic_status": epistemic_status} if epistemic_status else meta
        return NormalizedObservation(
            entity=handle, attribute=attribute, value=value, source=event.source,
            timestamp=when, confidence=confidence,
            evidence={"event": event.event_name, **(evidence or {})}, metadata=observation_meta,
        )

    out: list[NormalizedObservation] = [
        obs("event_name", event.event_name, 0.95),
        obs("category", event.category, 0.9),
        obs("in_region", event.region, 0.9, {"city": event.city, "country": event.country}),
        obs("occurs_at_venue", venue_handle(event), 0.9, {"venue_name": event.venue_name}),
        obs("lineup", event.artists, 0.85),
    ]
    if event.starts_at is not None:
        out.append(obs("starts_at", event.starts_at.isoformat(), 0.9))
    if event.ends_at is not None:
        out.append(obs("ends_at", event.ends_at.isoformat(), 0.9))
    if event.event_date is not None:
        out.append(obs("event_date", event.event_date, 0.9))
    if event.provider_lifecycle is not None:
        out.append(obs("provider_lifecycle", event.provider_lifecycle, 0.95))
    out.append(obs("source_time_precision", event.source_time_precision, 0.99, {
        "source_start_value": event.source_start_value, "source_end_value": event.source_end_value,
        "source_timezone": event.source_timezone,
    }))
    if event.price_min is not None:
        out.append(obs("price_min", event.price_min, 0.85, {"currency": event.currency, "is_free": event.is_free}))
    if event.image_url:
        out.append(obs("image_url", event.image_url, 0.8))
    if event.capacity is not None and event.tickets_sold is not None:
        # The demand ground truth. Higher confidence — it is a transacted count, not a proxy — but
        # it is a publicly DISPLAYED figure, so it is tagged observed_public_state, not verified sales.
        out.append(obs(
            "fill_ratio", event.fill_ratio, 0.9,
            {"tickets_sold": event.tickets_sold, "capacity": event.capacity},
            epistemic_status="observed_public_state",
        ))
    out.append(obs("source_event_id", event.source_event_id, 0.99, {"id_scheme": f"{event.source}_show_id"}))
    return out


def commercial_state(event: TicketingEvent) -> dict[str, Any]:
    """The mutable *public commercial state* of an event — the Shadow Ledger's observation unit
    (Phase 1.1 structured capture: values + per-field observation status + completeness).

    The adapter reports what it actually evaluated. Boshow returns a full show record, so this is a
    COMPLETE capture; fields the source does not expose (availability/status) are NOT_SUPPORTED.
    A field that is None is reported NOT_OBSERVED — a model default of None is NOT proof the source
    represented the field as empty, so it must never be inferred as OBSERVED_NULL. fill_ratio is an
    observed public state, never verified sell-through.
    """
    values = {
        "price_min": event.price_min,
        "currency": event.currency,
        "capacity": event.capacity,
        "tickets_sold": event.tickets_sold,
        "fill_ratio": event.fill_ratio,
        "availability": None,
        "starts_at": event.starts_at.isoformat() if event.starts_at else None,
        "ends_at": event.ends_at.isoformat() if event.ends_at else None,
        "event_date": event.event_date,
        "provider_lifecycle": event.provider_lifecycle,
        "source_time_precision": event.source_time_precision,
        "source_timezone": event.source_timezone,
        "source_start_value": event.source_start_value,
        "source_end_value": event.source_end_value,
        "venue": event.venue_name or None,
        "status": None,
    }

    def _status(v: Any) -> str:
        return "OBSERVED_VALUE" if v is not None else "NOT_OBSERVED"

    field_status = {
        "price_min": _status(event.price_min),
        "currency": _status(event.currency),
        "capacity": _status(event.capacity),
        "tickets_sold": _status(event.tickets_sold),
        "fill_ratio": _status(event.fill_ratio),
        "starts_at": _status(values["starts_at"]),
        "ends_at": _status(values["ends_at"]),
        "event_date": _status(values["event_date"]),
        "provider_lifecycle": _status(values["provider_lifecycle"]),
        "source_time_precision": _status(values["source_time_precision"]),
        "venue": _status(values["venue"]),
        "availability": "NOT_SUPPORTED",  # Boshow exposes no availability enum
        "status": "NOT_SUPPORTED",        # Boshow exposes no event-status/cancellation enum
    }
    return {
        "values": values,
        "field_status": field_status,
        "snapshot_completeness": "COMPLETE",
        "capture_status": "CAPTURE_SUCCESS_RECORD_PRESENT",
    }


class TicketingClient:
    def __init__(self, provider: str | None = None) -> None:
        self._provider = provider  # per-request source override (Phase 3); None -> configured default

    async def fetch_event(self, event_ref: str) -> TicketingEvent:
        return await get_provider(self._provider).extract(event_ref)

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        return await get_provider(self._provider).discover(city=city, limit=limit)
