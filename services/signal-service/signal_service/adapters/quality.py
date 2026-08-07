"""Ticketing record quality validation (Phase 4C).

Deterministic gate applied **before** a record is enrolled into scheduled capture. It rejects
placeholder / malformed / non-event records (the Skillbox probe found placeholder venues
"Mutiple Cities, India", a placeholder date 2029-07-28, and region duplicating city), validates
geography and dates against a verified city map, and reports per-field present/valid/specific status.

No graph events are created for rejected records; aggregate rejection counts + sampled reasons are kept
for diagnostics. This never fabricates values and never guesses coordinates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from signal_service.adapters.skillbox_cities import verified_city
from signal_service.adapters.ticketing import TicketingEvent

# ---- rejection reason codes ---------------------------------------------------------------------
MISSING_IDENTITY = "MISSING_IDENTITY"
PLACEHOLDER_EVENT = "PLACEHOLDER_EVENT"
PLACEHOLDER_DATE = "PLACEHOLDER_DATE"
INVALID_DATE = "INVALID_DATE"
GENERIC_LOCATION = "GENERIC_LOCATION"
NUMERIC_LOCATION_WITHOUT_MAPPING = "NUMERIC_LOCATION_WITHOUT_MAPPING"
MULTIPLE_CITIES_PLACEHOLDER = "MULTIPLE_CITIES_PLACEHOLDER"
MALFORMED_RECORD = "MALFORMED_RECORD"
CONTENT_NOT_EVENT = "CONTENT_NOT_EVENT"
SEO_OR_SPAM_PAGE = "SEO_OR_SPAM_PAGE"
DELETED_EVENT_SHELL = "DELETED_EVENT_SHELL"
UNSUPPORTED_EVENT_TYPE = "UNSUPPORTED_EVENT_TYPE"

# ---- verified geography (explicit map only — never guess) ----------------------------------------
IST = "Asia/Kolkata"
VERIFIED_CITIES: dict[str, tuple[str, str]] = {
    # city -> (region/state, timezone). India is single-tz, but keep the mapping explicit + per-city.
    "kolkata": ("West Bengal", IST), "howrah": ("West Bengal", IST),
    "mumbai": ("Maharashtra", IST), "pune": ("Maharashtra", IST), "navi mumbai": ("Maharashtra", IST),
    "delhi": ("Delhi", IST), "new delhi": ("Delhi", IST), "noida": ("Uttar Pradesh", IST),
    "gurugram": ("Haryana", IST), "gurgaon": ("Haryana", IST),
    "bengaluru": ("Karnataka", IST), "bangalore": ("Karnataka", IST),
    "chennai": ("Tamil Nadu", IST), "hyderabad": ("Telangana", IST),
    "ahmedabad": ("Gujarat", IST), "jaipur": ("Rajasthan", IST), "chandigarh": ("Chandigarh", IST),
    "goa": ("Goa", IST), "panaji": ("Goa", IST), "kochi": ("Kerala", IST),
}
# matches the real Skillbox placeholder "Mutiple Cities" (missing 'l') as well as "Multiple Cities".
_MULTI_CITY = re.compile(r"\bmu?l?tiple\s+cities\b", re.IGNORECASE)
_GENERIC_LOCATIONS = {"", "various", "various locations", "online", "tba", "tbd", "n/a", "na",
                      "india", "anywhere", "multiple venues", "to be announced", "to be decided",
                      "venue tba", "venue tbd", "coming soon"}
_SPAM_TOKENS = ("buy followers", "buy likes", "seo service", "casino", "loan approval", "escort",
                "call girl", "porn", "crypto giveaway", "forex signal")
_PLACEHOLDER_TITLES = {"", "test", "test event", "sample event", "untitled", "untitled event",
                       "demo event", "event name", "your event"}
_FAR_FUTURE_YEARS = 3  # a start >= now.year + this is treated as a placeholder date


@dataclass
class FieldStatus:
    present: bool = False
    valid: bool = False
    specific: bool = False


@dataclass
class ValidationResult:
    accepted: bool
    rejections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    geography: dict = field(default_factory=dict)
    date: dict = field(default_factory=dict)
    field_status: dict[str, FieldStatus] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "accepted": self.accepted, "rejections": self.rejections, "warnings": self.warnings,
            "geography": self.geography, "date": self.date,
            "field_status": {k: vars(v) for k, v in self.field_status.items()},
        }


def _is_numeric(value: str) -> bool:
    return bool(value) and value.strip().isdigit()


def classify_geography(event: TicketingEvent) -> dict:
    """Distinguish venue / locality / city / region; classify the city value. Never guesses coords."""
    city_raw = (event.city or "").strip()
    city_norm = city_raw.lower()
    verified = VERIFIED_CITIES.get(city_norm)
    # a stable source city id, verified from source evidence (Phase 4C.1), corroborates the name
    by_id = verified_city(getattr(event, "source_city_id", None))
    status = "UNRESOLVED"
    region = (event.region or "").strip() or None
    timezone = None
    if _MULTI_CITY.search(city_raw):
        status = "MULTIPLE_CITIES_PLACEHOLDER"
    elif _is_numeric(city_raw):
        status = "NUMERIC_ID"
    elif city_norm in _GENERIC_LOCATIONS:
        status = "GENERIC"
    elif verified:
        status = "VERIFIED"
        region, timezone = verified[0], verified[1]  # derived geography, kept separate from source region
    elif by_id:
        status = "VERIFIED_BY_ID"
        region, timezone = by_id[1], by_id[2]
    elif city_raw:
        status = "NAMED_UNVERIFIED"
    return {
        "venue_name": event.venue_name or None,
        "city": city_raw or None,
        "city_status": status,
        "source_city_id": getattr(event, "source_city_id", None),
        "source_region": (event.region or "").strip() or None,        # direct-source geography
        "derived_region": region if (verified or by_id) else None,    # from a verified map/id only
        "country": event.country or None,
        "timezone": timezone,
    }


def classify_date(event: TicketingEvent, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    starts = event.starts_at
    if starts is None:
        return {"starts_at": None, "present": False, "tz_aware": False, "plausible": False,
                "far_future_placeholder": False}
    tz_aware = starts.tzinfo is not None
    cmp = starts if tz_aware else starts.replace(tzinfo=UTC)  # naive treated as UTC for comparison only
    year_delta = starts.year - now.year
    far_future = year_delta >= _FAR_FUTURE_YEARS
    past = cmp < now.replace(hour=0, minute=0, second=0, microsecond=0)
    # implausible: far in the past (beyond a year's grace) — placeholder/expired shell
    implausible_past = starts.year < now.year - 1
    return {
        "starts_at": starts.isoformat(),
        "present": True,
        "tz_aware": tz_aware,
        "plausible": (not far_future) and (not implausible_past),
        "far_future_placeholder": far_future,
        "past": past,
    }


def normalize_timezone(event: TicketingEvent, geo: dict, *, warnings: list[str]) -> datetime | None:
    """Return a tz-aware start time. Uses the source tz when present; otherwise the verified-city tz
    ONLY (never a blind guess). Leaves a naive time naive (with a warning) when the city is unverified."""
    starts = event.starts_at
    if starts is None:
        return None
    if starts.tzinfo is not None:
        return starts
    tz = geo.get("timezone")
    if tz:
        return starts.replace(tzinfo=ZoneInfo(tz))
    warnings.append("naive datetime with unverified city — timezone not inferred")
    return starts


def _title_status(name: str) -> FieldStatus:
    n = (name or "").strip()
    return FieldStatus(present=bool(n), valid=n.lower() not in _PLACEHOLDER_TITLES,
                       specific=len(n) >= 4 and not n.isdigit())


def _venue_status(event: TicketingEvent, geo: dict) -> FieldStatus:
    v = (event.venue_name or "").strip()
    present = bool(v)
    generic = v.lower() in _GENERIC_LOCATIONS or bool(_MULTI_CITY.search(v))
    # a venue equal to the city string is not a specific venue
    specific = present and not generic and v.lower() != (event.city or "").lower()
    return FieldStatus(present=present, valid=present and not generic, specific=specific)


def _city_status(geo: dict) -> FieldStatus:
    st = geo["city_status"]
    present = bool(geo["city"])
    valid = st in ("VERIFIED", "VERIFIED_BY_ID", "NAMED_UNVERIFIED")
    return FieldStatus(present=present, valid=valid, specific=st in ("VERIFIED", "VERIFIED_BY_ID"))


def validate_ticketing_event(event: TicketingEvent, *, now: datetime | None = None) -> ValidationResult:
    """Deterministically accept or reject a record. Rejection is conservative (precision over recall)."""
    now = now or datetime.now(UTC)
    rejections: list[str] = []
    warnings: list[str] = []
    geo = classify_geography(event)
    date = classify_date(event, now=now)

    # identity
    if not (event.source_event_id or event.event_slug):
        rejections.append(MISSING_IDENTITY)

    # title / event-ness
    title = (event.event_name or "").strip()
    if title.lower() in _PLACEHOLDER_TITLES:
        rejections.append(PLACEHOLDER_EVENT)
    if any(tok in title.lower() for tok in _SPAM_TOKENS):
        rejections.append(SEO_OR_SPAM_PAGE)

    # geography
    if geo["city_status"] == "MULTIPLE_CITIES_PLACEHOLDER":
        rejections.append(MULTIPLE_CITIES_PLACEHOLDER)
    elif geo["city_status"] == "NUMERIC_ID":
        rejections.append(NUMERIC_LOCATION_WITHOUT_MAPPING)
    elif geo["city_status"] == "GENERIC" and not (event.venue_name and event.venue_name.strip()):
        rejections.append(GENERIC_LOCATION)

    # date
    if date["present"] and not date["plausible"]:
        rejections.append(PLACEHOLDER_DATE if date["far_future_placeholder"] else INVALID_DATE)

    # deleted / empty shell: unverified + no date + no venue + no price
    if (not event.verified and not date["present"] and not (event.venue_name or "").strip()
            and event.price_min is None and not event.artists):
        rejections.append(DELETED_EVENT_SHELL)

    # content-not-event: nothing event-like at all (no date, no venue, no price, no artists)
    if (not date["present"] and not (event.venue_name or "").strip()
            and event.price_min is None and not event.artists and MISSING_IDENTITY not in rejections):
        rejections.append(CONTENT_NOT_EVENT)

    field_status = {
        "title": _title_status(title),
        "venue": _venue_status(event, geo),
        "city": _city_status(geo),
        "date": FieldStatus(present=date["present"], valid=date["plausible"],
                            specific=date["present"] and date["tz_aware"]),
    }
    if not date["tz_aware"] and date["present"]:
        normalize_timezone(event, geo, warnings=warnings)  # records a warning if unverifiable

    return ValidationResult(accepted=not rejections, rejections=sorted(set(rejections)),
                            warnings=warnings, geography=geo, date=date, field_status=field_status)
