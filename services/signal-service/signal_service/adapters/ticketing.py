"""Ticketing adapter — live-event supply + demand ground truth.

Ticketing is the closest thing to commercial truth in this domain: an event's fill ratio
(tickets sold / capacity) is real demand, not a search proxy. The dominant hub (BookMyShow)
is bot-walled with no public API, so it is parked as ``partner_feed`` (needs a deal). The
regional players are far more open — **Boshow** (Kolkata-rooted grassroots platform) exposes an
unauthenticated form-encoded search API that uniquely returns ``tickets_sold`` and capacity per show.

No single canonical API exists, so the provider is pluggable (like Google Trends):
  - ``mock``     — default; seeded with real Boshow-shaped records, so tests are faithful.
  - ``boshow``   — real, public_scrape of the unauthenticated /api/search JSON.
  - ``district`` / ``skillbox`` — sitemap-driven (next); same interface.
  - ``bookmyshow`` — partner_feed scaffold; raises until a data partnership exists.

Every provider yields a provider-neutral ``TicketingEvent``. One event is inherently
multi-entity — an event, a venue, and a lineup of artists — so this adapter is the first to
populate *structural* graph relationships (OCCURS_AT / FEATURES / IN_REGION), and the artists
resolve by name into the same canonical entities the YouTube/Trends pipelines produce.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol

import httpx

from signal_service.config import settings
from signal_service.schemas import NormalizedObservation

ADAPTER_VERSION = "ticketing-v1"

DISTRICT_BASE = "https://www.district.in"
SKILLBOX_BASE = "https://www.skillboxes.com"
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}

# Boshow separates performers in a lineup string with a bullet.
_LINEUP_SEP = re.compile(r"\s*[•|]\s*")


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


def event_from_district(node: dict[str, Any], url: str, *, fetched_at: datetime | None = None) -> TicketingEvent:
    """Map a District schema.org Event (JSON-LD) to the canonical TicketingEvent."""
    loc = node.get("location") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    venue = loc.get("name", "") if isinstance(loc, dict) else ""
    addr = loc.get("address", "") if isinstance(loc, dict) else ""
    if isinstance(addr, dict):
        city, region = addr.get("addressLocality", ""), addr.get("addressRegion", "")
    else:
        city, region = _india_city_region(addr)

    offers = node.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    price = _to_float(offers.get("lowPrice") or offers.get("price")) if isinstance(offers, dict) else None
    currency = offers.get("priceCurrency", "INR") if isinstance(offers, dict) else "INR"

    perf = node.get("performer") or []
    if isinstance(perf, dict):
        perf = [perf]
    artists = [p["name"].strip() for p in perf if isinstance(p, dict) and p.get("name")]
    org = node.get("organizer") or {}
    keywords = node.get("keywords")
    slug = url.rstrip("/").split("/events/")[-1]
    return TicketingEvent(
        source="district", source_event_id=slug, event_slug=slug,
        event_name=(node.get("name") or "").strip(), event_url=url,
        city=city, region=region, country="India", venue_name=(venue or "").strip(),
        artists=artists, curator=org.get("name") if isinstance(org, dict) else None,
        category=(keywords.split(",")[0].strip() if isinstance(keywords, str) and keywords else "Event"),
        language="", currency=currency or "INR", price_min=price,
        is_free=(price == 0.0), starts_at=_parse_dt(node.get("startDate")),
        capacity=None, tickets_sold=None, verified=True, fetched_at=fetched_at or datetime.now(UTC),
    )


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
        verified=bool(data.get("status")), fetched_at=fetched_at or datetime.now(UTC),
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
    },
}


class MockTicketingProvider:
    name = "mock"

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        return list(_MOCK_BOSHOW.keys())[:limit]

    async def extract(self, event_ref: str) -> TicketingEvent:
        item = _MOCK_BOSHOW.get(event_ref)
        if item is None:
            raise ValueError(f"unknown mock event: {event_ref}")
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
        raise ValueError(f"boshow: event not found for ref {event_ref!r}")


def _sitemap_slugs(xml: str, limit: int) -> list[str]:
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    slugs = [loc.rstrip("/").split("/events/")[-1] for loc in locs if "/events/" in loc]
    return slugs[:limit]


class DistrictProvider:
    """public_scrape of District (Zomato). robots-allowed; discovery via the events sitemap,
    extraction from each page's schema.org Event JSON-LD. Plain httpx (SSR pages)."""

    name = "district"

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        url = f"{DISTRICT_BASE}/events/search-sitemap/event-detail-pages.xml"
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            return _sitemap_slugs(r.text, limit)

    async def extract(self, event_ref: str) -> TicketingEvent:
        url = event_ref if event_ref.startswith("http") else f"{DISTRICT_BASE}/events/{event_ref}"
        async with httpx.AsyncClient(timeout=25.0, headers=_BROWSER_HEADERS, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            events = _jsonld_events(r.text)
        if not events:
            raise ValueError(f"district: no Event JSON-LD at {url}")
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
            raise ValueError(f"skillbox: event not found for slug {event_ref!r}")
        return event_from_skillbox(payload["data"])


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


def get_provider() -> TicketingProvider:
    provider = settings.ticketing_provider.lower()
    if provider == "boshow":
        return BoshowProvider()
    if provider == "district":
        return DistrictProvider()
    if provider == "skillbox":
        return SkillboxProvider()
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

    def obs(attribute: str, value: Any, confidence: float, evidence: dict[str, Any] | None = None) -> NormalizedObservation:
        return NormalizedObservation(
            entity=handle, attribute=attribute, value=value, source=event.source,
            timestamp=when, confidence=confidence,
            evidence={"event": event.event_name, **(evidence or {})}, metadata=meta,
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
    if event.price_min is not None:
        out.append(obs("price_min", event.price_min, 0.85, {"currency": event.currency, "is_free": event.is_free}))
    if event.capacity is not None and event.tickets_sold is not None:
        # The demand ground truth. Higher confidence — it is a transacted count, not a proxy.
        out.append(obs(
            "fill_ratio", event.fill_ratio, 0.9,
            {"tickets_sold": event.tickets_sold, "capacity": event.capacity},
        ))
    out.append(obs("source_event_id", event.source_event_id, 0.99, {"id_scheme": f"{event.source}_show_id"}))
    return out


class TicketingClient:
    async def fetch_event(self, event_ref: str) -> TicketingEvent:
        return await get_provider().extract(event_ref)

    async def discover(self, *, city: str | None = None, limit: int = 20) -> list[str]:
        return await get_provider().discover(city=city, limit=limit)
