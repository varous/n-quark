"""Pure, deterministic extractors for Boshow public-page structured metadata (Phase 2.1).

No network, no LLM, no bs4. Each returns a list of `Candidate` for fields actually present — a
missing field yields no candidate; a parse failure is reported to the caller (empty list) and never
becomes a null candidate. Structured event selection matches by title similarity, never "first one".
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

from crawl_service.enrichment.registry import (
    JSON_LD,
    OPEN_GRAPH,
    PAGE_METADATA,
    STRUCTURED_DATA,
    TEXT_PARSE,
    VISIBLE_TEXT,
    Candidate,
)

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL
)
_OG_RE = re.compile(
    r'<meta[^>]+property=["\']og:([a-z0-9_:]+)["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE
)
_EMBEDDED_RE = re.compile(
    r'(?:window\.__(?:NQ|APP|DATA|INITIAL_STATE)__|__NEXT_DATA__["\']?\s*\]?)\s*=\s*(\{.*?\})\s*;?\s*</script>',
    re.DOTALL,
)


def _slugify(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (v or "").lower()).strip("-")


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def parse_jsonld_events(html: str) -> list[dict[str, Any]]:
    """Return every schema.org Event object found in <script type=application/ld+json> blocks,
    handling single objects, arrays and @graph. Malformed blocks are skipped, not fatal."""
    events: list[dict[str, Any]] = []
    for block in _JSONLD_RE.findall(html or ""):
        try:
            data = json.loads(block.strip())
        except (ValueError, TypeError):
            continue  # invalid JSON-LD -> skip safely
        for node in _iter_nodes(data):
            if _is_event(node):
                events.append(node)
    return events


def _iter_nodes(data: Any):
    if isinstance(data, list):
        for item in data:
            yield from _iter_nodes(item)
    elif isinstance(data, dict):
        if "@graph" in data and isinstance(data["@graph"], list):
            for item in data["@graph"]:
                yield from _iter_nodes(item)
        else:
            yield data


def _is_event(node: dict) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.endswith("Event") for x in types)


def select_event(events: list[dict], *, title: str | None, source_record_id: str | None) -> dict | None:
    """Choose the Event object that best matches the known title / source record — not the first."""
    if not events:
        return None
    if len(events) == 1:
        return events[0]
    best, best_score = None, -1.0
    for ev in events:
        name = ev.get("name") or ""
        score = _similar(name, title or "")
        if source_record_id and _slugify(name) and _slugify(name) in source_record_id:
            score += 0.25
        if score > best_score:
            best, best_score = ev, score
    return best


def _venue_from_location(loc: Any) -> tuple[str | None, str | None]:
    """Return (venue_name, city) from a schema.org location value."""
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if isinstance(loc, dict):
        name = loc.get("name")
        addr = loc.get("address")
        city = addr.get("addressLocality") if isinstance(addr, dict) else None
        return name, city
    if isinstance(loc, str):
        return loc, None
    return None, None


def candidates_from_jsonld(
    html: str, *, title: str | None, source_record_id: str | None, source_url: str | None,
    observed_at: datetime,
) -> list[Candidate]:
    ev = select_event(parse_jsonld_events(html), title=title, source_record_id=source_record_id)
    if ev is None:
        return []
    out: list[Candidate] = []

    def add(field_name: str, value: Any, conf: float) -> None:
        if value not in (None, ""):
            out.append(Candidate(
                field_name=field_name, candidate_value=value, source_type=JSON_LD,
                extraction_method=STRUCTURED_DATA, confidence=conf, observed_at=observed_at,
                source_url=source_url,
            ).normalize())

    add("starts_at", ev.get("startDate"), 0.85)
    add("end_at", ev.get("endDate"), 0.8)
    status = ev.get("eventStatus")
    if isinstance(status, str):
        add("event_status", status.rsplit("/", 1)[-1], 0.75)
    venue_name, city = _venue_from_location(ev.get("location"))
    add("venue_name", venue_name, 0.75)
    add("city", city, 0.65)
    return [c for c in out if c.normalized_value is not None]


def candidates_from_opengraph(
    html: str, *, source_url: str | None, observed_at: datetime,
) -> list[Candidate]:
    og = {k.lower(): v for k, v in _OG_RE.findall(html or "")}
    out: list[Candidate] = []
    # OG has low authority for dates/venue; only emit where a mapped field exists.
    mapping = {
        "event:start_time": ("starts_at", 0.55),
        "event:end_time": ("end_at", 0.5),
    }
    for og_key, (field_name, conf) in mapping.items():
        if og_key in og:
            c = Candidate(
                field_name=field_name, candidate_value=og[og_key], source_type=OPEN_GRAPH,
                extraction_method=PAGE_METADATA, confidence=conf, observed_at=observed_at,
                source_url=source_url,
            ).normalize()
            if c.normalized_value is not None:
                out.append(c)
    return out


_LABEL_PATTERNS = {
    "venue_name": re.compile(r'(?:Venue|Location)\s*[:\-]\s*([^<\n\r|]{2,120})', re.IGNORECASE),
    "city": re.compile(r'City\s*[:\-]\s*([^<\n\r|]{2,80})', re.IGNORECASE),
    "starts_at": re.compile(r'(?:Date|When)\s*[:\-]\s*([^<\n\r|]{4,80})', re.IGNORECASE),
}


def candidates_from_visible_text(
    text: str, *, source_url: str | None, observed_at: datetime,
) -> list[Candidate]:
    """Deterministic parse of clearly-labelled fields only. No broad NLP."""
    out: list[Candidate] = []
    for field_name, pattern in _LABEL_PATTERNS.items():
        m = pattern.search(text or "")
        if not m:
            continue
        c = Candidate(
            field_name=field_name, candidate_value=m.group(1).strip(), source_type=VISIBLE_TEXT,
            extraction_method=TEXT_PARSE, confidence=0.45, observed_at=observed_at,
            source_url=source_url,
        ).normalize()
        if c.normalized_value is not None:
            out.append(c)
    return out


def candidates_from_embedded_state(
    html: str, *, source_url: str | None, observed_at: datetime,
) -> list[Candidate]:
    """Extract from public embedded application state (no auth bypass). Best-effort JSON parse."""
    m = _EMBEDDED_RE.search(html or "")
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except (ValueError, TypeError):
        return []
    out: list[Candidate] = []
    flat = data if isinstance(data, dict) else {}

    def add(field_name: str, value: Any, conf: float) -> None:
        if value not in (None, ""):
            c = Candidate(
                field_name=field_name, candidate_value=value, source_type="EMBEDDED_STATE",
                extraction_method=STRUCTURED_DATA, confidence=conf, observed_at=observed_at,
                source_url=source_url,
            ).normalize()
            if c.normalized_value is not None:
                out.append(c)

    add("starts_at", flat.get("startDate") or flat.get("start_date") or flat.get("real_show_date"), 0.8)
    add("venue_name", flat.get("venue") or flat.get("location"), 0.7)
    add("event_status", flat.get("status") or flat.get("eventStatus"), 0.7)
    return out
