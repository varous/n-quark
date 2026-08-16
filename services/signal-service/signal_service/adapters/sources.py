"""Validated discovery + per-source quality metrics (Phase 4C).

Drives the shared adapter contract to produce an *accepted* set of records (placeholder/malformed/
non-event records rejected before enrollment) and a per-source quality report. Discovery-time metrics
only — capture-time metrics (record-present, transitions, gap, entity-resolution rate) come from the
capture pipeline (crawl-service + the admin BFF). Bounded and honest: never total-market coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from signal_service.adapters.contract import get_adapter
from signal_service.adapters.quality import ValidationResult, validate_ticketing_event
from signal_service.config import settings

CITY_FILTER_MISMATCH = "CITY_FILTER_MISMATCH"

SOURCE_DESCRIPTORS: dict[str, dict[str, Any]] = {
    "district": {"source_role": "PRIMARY_DISCOVERY", "acquisition_mode": "PUBLIC_SSR_JSONLD",
                 "provenance_level": "SOURCE_PAGE", "automation_posture": "ROBOTS_ALLOWED_PUBLIC_PAGES",
                 "continuous_collection_allowed": True,
                 "claim_authority": ["SCHEDULE", "VENUE", "ORGANIZER", "TICKETING", "LIFECYCLE"],
                 "policy_checked_at": "2026-08-16", "policy_reference": "https://www.district.in/robots.txt",
                 "disposition": "PRODUCTION"},
    "boshow": {"source_role": "PRIMARY_DISCOVERY", "acquisition_mode": "PUBLIC_ANONYMOUS_SEARCH_API",
               "provenance_level": "SOURCE_API", "automation_posture": "PUBLIC_LOGGED_OUT_ENDPOINT",
               "continuous_collection_allowed": True,
               "claim_authority": ["SCHEDULE", "VENUE", "LINEUP", "TICKETING"],
               "policy_checked_at": "2026-08-16", "policy_reference": "https://www.boshow.in/robots.txt",
               "disposition": "PRODUCTION"},
    "allevents": {"source_role": "SUPPLEMENTARY_EVIDENCE", "acquisition_mode": "OFFICIAL_API_PENDING",
                  "provenance_level": "AGGREGATOR", "automation_posture": "AUTHORIZED_API_REQUIRED",
                  "continuous_collection_allowed": False, "claim_authority": ["DISCOVERY"],
                  "policy_checked_at": "2026-08-14", "policy_reference": "https://allevents.in/pages/events-api",
                  "disposition": "ACCESS_PENDING"},
}


def source_descriptors() -> list[dict[str, Any]]:
    return [{"source": key, **value} for key, value in SOURCE_DESCRIPTORS.items()]


@dataclass
class AcceptedRecord:
    event_ref: str
    source_event_id: str
    title: str
    city: str | None
    venue: str | None
    starts_at: str | None
    image_url: str | None
    source_url: str


@dataclass
class RejectedRecord:
    event_ref: str
    reasons: list[str]
    source_url: str | None
    fetch_error: bool = False


@dataclass
class DiscoveryReport:
    source: str
    city_filter: str | None
    candidates_considered: int
    accepted: list[AcceptedRecord] = field(default_factory=list)
    rejected: list[RejectedRecord] = field(default_factory=list)
    out_of_scope: list[RejectedRecord] = field(default_factory=list)
    field_totals: dict[str, dict[str, int]] = field(default_factory=dict)
    available: bool = True
    error: str | None = None

    def rejections_by_reason(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.rejected:
            for reason in r.reasons:
                counts[reason] = counts.get(reason, 0) + 1
        return dict(sorted(counts.items()))


def _accumulate_fields(totals: dict[str, dict[str, int]], vr: ValidationResult) -> None:
    for fname, fs in vr.field_status.items():
        t = totals.setdefault(fname, {"considered": 0, "present": 0, "valid": 0, "specific": 0})
        t["considered"] += 1
        t["present"] += int(fs.present)
        t["valid"] += int(fs.valid)
        t["specific"] += int(fs.specific)


async def validated_discovery(source: str, *, city: str | None = None, limit: int = 10,
                              fetch_cap: int | None = None, now: datetime | None = None) -> DiscoveryReport:
    """Discover candidate refs, fetch each (bounded), validate, and city-filter. Records that fail
    validation are rejected (with reasons); records outside the city scope are out_of_scope."""
    now = now or datetime.now(UTC)
    fetch_cap = fetch_cap or settings.quality_fetch_cap
    adapter = get_adapter(source)
    report = DiscoveryReport(source=source, city_filter=city, candidates_considered=0)
    try:
        candidate_refs = await adapter.discover(city=city, limit=max(fetch_cap, limit))
    except Exception as exc:  # noqa: BLE001 — surface source-unavailable, do not 500
        report.available = False
        report.error = f"{type(exc).__name__}: {exc}"
        return report

    for ref in candidate_refs[:fetch_cap]:
        report.candidates_considered += 1
        try:
            event = await adapter.fetch_event(ref)
        except Exception as exc:  # noqa: BLE001
            report.rejected.append(RejectedRecord(ref, [adapter.classify_failure(exc)], None, True))
            continue
        vr = validate_ticketing_event(event, now=now)
        _accumulate_fields(report.field_totals, vr)
        city_ok = (not city) or ((event.city or "").strip().lower() == city.strip().lower())
        if vr.accepted and city_ok:
            report.accepted.append(AcceptedRecord(
                event_ref=ref, source_event_id=event.source_event_id, title=event.event_name,
                city=event.city or None, venue=event.venue_name or None,
                starts_at=event.starts_at.isoformat() if event.starts_at else None,
                image_url=event.image_url, source_url=event.event_url))
            if len(report.accepted) >= limit:
                break
        elif not vr.accepted:
            report.rejected.append(RejectedRecord(ref, vr.rejections, event.event_url))
        else:  # accepted quality but outside the requested city
            report.out_of_scope.append(RejectedRecord(ref, [CITY_FILTER_MISMATCH], event.event_url))
    return report


def managed_sources() -> list[str]:
    return [s.strip() for s in settings.ticketing_managed_sources.split(",") if s.strip()]


def source_enabled(source: str) -> bool:
    if source == "skillbox":
        return settings.skillbox_enabled
    return True  # boshow/district are always available adapters


def quality_report(report: DiscoveryReport) -> dict[str, Any]:
    considered = report.candidates_considered
    def _cov(t: dict[str, int], key: str) -> float | None:
        return round(t[key] / t["considered"], 3) if t.get("considered") else None
    return {
        "source": report.source,
        "city_filter": report.city_filter,
        "available": report.available,
        "error": report.error,
        "records_discovered": considered,
        "records_accepted": len(report.accepted),
        "records_rejected": len(report.rejected),
        "records_out_of_scope": len(report.out_of_scope),
        "rejections_by_reason": report.rejections_by_reason(),
        # field state kept distinct: present vs valid vs specific (resolved is a capture-time metric)
        "field_quality": {
            fname: {"present": _cov(t, "present"), "valid": _cov(t, "valid"),
                    "specific": _cov(t, "specific"), "considered": t["considered"]}
            for fname, t in sorted(report.field_totals.items())
        },
        "note": "Discovery-time observed quality — not total-market coverage. Capture-time metrics "
                "(record-present, transitions, gap, entity-resolution rate) come from the capture pipeline.",
    }
