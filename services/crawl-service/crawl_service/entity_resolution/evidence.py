"""Source-event entity evidence extraction (Phase 3.1).

Reads one captured event (its canonical graph node + 1-hop neighbours) and derives the entity
evidence it carries — performers, venue, organizer, and a possible recurring series. Every evidence
item keeps its source, raw value, normalized value, extraction method, confidence and provenance;
a missing field produces no evidence (never a guessed one).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from crawl_service.entity_resolution import normalizers as N

ARTIST = "ARTIST"
VENUE = "VENUE"
ORGANIZER = "ORGANIZER"
EVENT_SERIES = "EVENT_SERIES"

EXTRACTION_VERSION = "entity-evidence-1"


@dataclass
class EntityEvidence:
    entity_type: str
    source: str
    source_record_id: str
    canonical_event_id: str
    source_entity_handle: str
    raw_name: str
    normalized_name: str
    observed_at: datetime
    confidence: float
    extraction_method: str = EXTRACTION_VERSION
    provenance: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)


def _handle(source: str, entity_type: str, raw: str) -> str:
    return f"{source}:{entity_type.lower()}:{N.slug(raw)}"


@dataclass
class EventEntities:
    canonical_event_id: str
    source: str
    source_record_id: str
    city: str | None
    region_id: str | None
    artists: list[EntityEvidence] = field(default_factory=list)
    venue: EntityEvidence | None = None
    organizer: EntityEvidence | None = None
    series: EntityEvidence | None = None

    def all(self) -> list[EntityEvidence]:
        out = list(self.artists)
        for e in (self.venue, self.organizer, self.series):
            if e is not None:
                out.append(e)
        return out


def extract_event_entities(
    *, canonical_event_id: str, source: str, source_record_id: str,
    node: dict[str, Any] | None, neighbors: list[dict[str, Any]], observed_at: datetime,
) -> EventEntities:
    props = (node or {}).get("properties", {}) if node else {}
    title = (props.get("display_name") or "").strip()
    city = props.get("city") or None
    source_url = props.get("source_url")
    prov = {"source_url": source_url} if source_url else {}

    venue_name = region_id = None
    performer_names: list[str] = []
    for nb in neighbors or []:
        rel, n = nb.get("relationship"), nb.get("node") or {}
        nm = (n.get("properties") or {}).get("display_name")
        if rel == "OCCURS_AT":
            venue_name = nm or (n.get("id") or "").split(":")[-1]
        elif rel == "IN_REGION":
            region_id = n.get("id")
        elif rel == "FEATURES" and nm:
            performer_names.append(nm)

    ev = EventEntities(canonical_event_id=canonical_event_id, source=source,
                       source_record_id=source_record_id, city=city, region_id=region_id)

    # ---- artists (FEATURES) ---------------------------------------------------------------------
    for name in performer_names:
        an = N.normalize_artist(name)
        if not an.normalized:
            continue
        ev.artists.append(EntityEvidence(
            entity_type=ARTIST, source=source, source_record_id=source_record_id,
            canonical_event_id=canonical_event_id, source_entity_handle=_handle(source, ARTIST, name),
            raw_name=an.raw, normalized_name=an.normalized, observed_at=observed_at,
            confidence=0.8, provenance=prov,
            evidence={"is_tribute": an.is_tribute, "is_ambiguous": N.is_ambiguous_artist(an),
                      "stripped_feat": an.stripped_feat, "city": city},
        ))

    # ---- venue (OCCURS_AT) ----------------------------------------------------------------------
    if venue_name and venue_name.strip():
        vn = N.normalize_venue(venue_name)
        if vn.normalized:
            ev.venue = EntityEvidence(
                entity_type=VENUE, source=source, source_record_id=source_record_id,
                canonical_event_id=canonical_event_id,
                source_entity_handle=_handle(source, VENUE, venue_name),
                raw_name=vn.raw, normalized_name=vn.normalized, observed_at=observed_at,
                confidence=0.85, provenance=prov,
                evidence={"city": city, "region_id": region_id, "is_generic": vn.is_generic},
            )

    # ---- organizer (event property, from schema.org organizer / Boshow curator) -----------------
    organizer_name = props.get("organizer")
    if organizer_name and str(organizer_name).strip():
        on = N.normalize_organizer(str(organizer_name))
        if on.normalized:
            ev.organizer = EntityEvidence(
                entity_type=ORGANIZER, source=source, source_record_id=source_record_id,
                canonical_event_id=canonical_event_id,
                source_entity_handle=_handle(source, ORGANIZER, str(organizer_name)),
                raw_name=on.raw, normalized_name=on.normalized, observed_at=observed_at,
                confidence=0.7, provenance=prov, evidence={"city": city},
            )

    # ---- event series (derived from the event title + organizer/venue context) ------------------
    if title:
        sn = N.normalize_series(title)
        # Only a title that actually looks recurring (an edition/year/vol marker) is series evidence;
        # a one-off title with no recurrence marker produces none.
        if sn.series_normalized and sn.markers:
            ev.series = EntityEvidence(
                entity_type=EVENT_SERIES, source=source, source_record_id=source_record_id,
                canonical_event_id=canonical_event_id,
                source_entity_handle=_handle(source, EVENT_SERIES, sn.series_normalized),
                raw_name=title, normalized_name=sn.series_normalized, observed_at=observed_at,
                confidence=0.6, provenance=prov,
                evidence={"edition_label": sn.edition_label, "edition_number": sn.edition_number,
                          "is_generic": sn.is_generic, "markers": sn.markers,
                          "organizer": (organizer_name or None), "city": city},
            )

    return ev
