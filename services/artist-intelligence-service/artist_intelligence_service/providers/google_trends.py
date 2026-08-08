"""Google Trends providers (Phase 5A) — feature-gated, provider-neutral, NO scraping.

Two modes:
- ``GoogleTrendsProvider`` (OFFICIAL_API): used only with valid alpha credentials + endpoint. Without
  them it reports ``ACCESS_UNAVAILABLE`` and never fabricates endpoint shapes.
- ``GoogleTrendsImportProvider`` (IMPORT): structured ingestion of CSV exports obtained legitimately
  from the Google Trends UI. It preserves original values, query/topic, geography, date range, export
  timestamp, normalization context, and source-file provenance — and never pretends the data came from
  the API.

Trends values are RELATIVE search interest (0–100 within a single pull), never absolute volume, and two
independently normalized exports must never be compared as if they shared a scale.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from artist_intelligence_service.config import settings
from artist_intelligence_service.providers.base import (
    CAP_GEOGRAPHIC,
    CAP_HISTORICAL,
    CAP_IMPORT,
    GOOGLE_SEARCH_INTEREST,
    IMPORTED_PROVIDER_EXPORT,
    PROVIDER_GOOGLE_TRENDS,
    ArtistIntelligenceProvider,
    DemandDatum,
    ProviderAccessUnavailable,
)

TRENDS_UNIT = "relative_interest_0_100"

# ISO 3166-2:IN codes for Indian states/UTs, so geography is first-class without inventing precision.
# Unmapped regions keep a slug scope_id + provenance note; the provider's granularity is preserved.
_IN_REGION_ISO = {
    "andhra pradesh": "IN-AP", "arunachal pradesh": "IN-AR", "assam": "IN-AS", "bihar": "IN-BR",
    "chhattisgarh": "IN-CT", "goa": "IN-GA", "gujarat": "IN-GJ", "haryana": "IN-HR",
    "himachal pradesh": "IN-HP", "jharkhand": "IN-JH", "karnataka": "IN-KA", "kerala": "IN-KL",
    "madhya pradesh": "IN-MP", "maharashtra": "IN-MH", "manipur": "IN-MN", "meghalaya": "IN-ML",
    "mizoram": "IN-MZ", "nagaland": "IN-NL", "odisha": "IN-OR", "punjab": "IN-PB",
    "rajasthan": "IN-RJ", "sikkim": "IN-SK", "tamil nadu": "IN-TN", "telangana": "IN-TG",
    "tripura": "IN-TR", "uttar pradesh": "IN-UP", "uttarakhand": "IN-UT", "west bengal": "IN-WB",
    "delhi": "IN-DL", "national capital territory of delhi": "IN-DL", "chandigarh": "IN-CH",
    "puducherry": "IN-PY", "jammu and kashmir": "IN-JK", "ladakh": "IN-LA",
}


def region_scope(name: str, *, country: str) -> tuple[str, str, bool]:
    """(scope_id, scope_label, mapped). Preserves the provider's exact label; maps to ISO where known."""
    iso = _IN_REGION_ISO.get(name.strip().lower()) if country.upper() == "IN" else None
    if iso:
        return iso, name, True
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown"
    return f"{country.upper()}-{slug}", name, False


class TrendsImportError(ValueError):
    """A Trends CSV export could not be parsed into recognizable interest data."""


@dataclass
class ParsedTrends:
    kind: str                                 # "TIMESERIES" | "GEO"
    label: str                                # value-column header (e.g. "arijit singh: (India)")
    aggregation: str | None = None            # Day | Week | Month (timeseries only)
    rows: list[tuple[str, int]] = field(default_factory=list)   # (date|region, value)


def parse_trends_csv(text: str) -> ParsedTrends:
    """Auto-detect and parse a Google Trends UI export (interest-over-time OR interest-by-region).

    Trends exports carry a preamble/blank lines before a header row; the first data header's left
    column is 'Day'/'Week'/'Month' (timeseries) or 'Region'/'Country'/... (geo)."""
    lines = [ln for ln in (text or "").splitlines()]
    # Locate the header row: the first row whose first cell is a known dimension keyword.
    time_keys = {"day", "week", "month", "date", "time"}
    geo_keys = {"region", "country", "subregion", "city", "metro", "state", "location"}
    header_idx = None
    kind = None
    for i, ln in enumerate(lines):
        cell0 = (ln.split(",")[0] or "").strip().strip('"').lower()
        if cell0 in time_keys:
            header_idx, kind = i, "TIMESERIES"
            break
        if cell0 in geo_keys:
            header_idx, kind = i, "GEO"
            break
    if header_idx is None:
        raise TrendsImportError("no recognizable Trends header row (expected Day/Week/Month or Region)")

    reader = list(csv.reader(io.StringIO("\n".join(lines[header_idx:]))))
    if not reader or len(reader[0]) < 2:
        raise TrendsImportError("Trends header has no value column")
    header = reader[0]
    label = (header[1] or "").strip()
    aggregation = header[0].strip().capitalize() if kind == "TIMESERIES" else None

    rows: list[tuple[str, int]] = []
    for raw in reader[1:]:
        if len(raw) < 2 or not (raw[0] or "").strip():
            continue
        key = raw[0].strip()
        val_s = (raw[1] or "").strip().replace("<", "").replace("%", "")
        if val_s == "" or key.lower() in time_keys or key.lower() in geo_keys:
            continue
        try:
            val = round(float(val_s))
        except ValueError:
            # A stray non-numeric row is tolerated only if others parse; tracked for rejection below.
            continue
        rows.append((key, val))
    if not rows:
        raise TrendsImportError("Trends export contained no parseable numeric rows")
    return ParsedTrends(kind=kind, label=label, aggregation=aggregation, rows=rows)


class GoogleTrendsImportProvider(ArtistIntelligenceProvider):
    """IMPORT mode — parse legitimately-obtained CSV exports into demand observations."""

    name = PROVIDER_GOOGLE_TRENDS
    capabilities = frozenset({CAP_IMPORT})

    def build_data(
        self, parsed: ParsedTrends, *, identity_type: str, provider_id: str, geo: str,
        time_range: str | None, comparison_window: str | None, source_file: str | None,
        export_timestamp: str | None,
    ) -> list[DemandDatum]:
        """Convert a ParsedTrends into DemandData with full normalization context + provenance.

        evidence_status = IMPORTED_PROVIDER_EXPORT (distinguishable from OFFICIAL_API data). The
        search-term-vs-topic interpretation is carried in provenance.identity_basis + identity_type."""
        basis = "TOPIC_BASED" if identity_type == "TOPIC_ID" else "SEARCH_TERM_BASED"
        base_prov = {
            "provider": PROVIDER_GOOGLE_TRENDS, "provider_mode": "IMPORT",
            "acquisition_method": "user_export_import", "identity_basis": basis,
            "identity_type": identity_type, "provider_id": provider_id, "query_or_topic": parsed.label,
            "geo": geo, "time_range": time_range, "aggregation": parsed.aggregation,
            "normalization": "trends_0_100_within_pull", "comparison_window": comparison_window,
            "source_file": source_file, "export_timestamp": export_timestamp,
            "scale_note": "relative interest 0-100 within THIS pull; not comparable across pulls",
        }
        out: list[DemandDatum] = []
        if parsed.kind == "GEO":
            for region_name, value in parsed.rows:
                scope_id, scope_label, mapped = region_scope(region_name, country=geo)
                out.append(DemandDatum(
                    metric=GOOGLE_SEARCH_INTEREST, value_numeric=value, unit=TRENDS_UNIT,
                    scope_type="REGION", scope_id=scope_id, scope_label=scope_label,
                    evidence_status=IMPORTED_PROVIDER_EXPORT, dedup_extra=f"geo:{scope_id}",
                    provenance={**base_prov, "region_iso_mapped": mapped, "region_raw": region_name}))
        else:  # TIMESERIES
            for date_s, value in parsed.rows:
                ts = _parse_date(date_s)
                out.append(DemandDatum(
                    metric=GOOGLE_SEARCH_INTEREST, value_numeric=value, unit=TRENDS_UNIT,
                    scope_type="COUNTRY", scope_id=geo.upper(), scope_label=geo.upper(),
                    provider_timestamp=ts, evidence_status=IMPORTED_PROVIDER_EXPORT,
                    dedup_extra=f"ts:{date_s}", provenance={**base_prov, "date": date_s}))
        return out


class GoogleTrendsProvider(ArtistIntelligenceProvider):
    """OFFICIAL_API mode — disabled unless valid alpha credentials + endpoint are configured.

    Without them, every operation raises ProviderAccessUnavailable (surfaced as ACCESS_UNAVAILABLE);
    we do NOT fabricate endpoint shapes from inaccessible alpha docs."""

    name = PROVIDER_GOOGLE_TRENDS
    capabilities = frozenset({CAP_GEOGRAPHIC, CAP_HISTORICAL})

    @property
    def available(self) -> bool:
        return settings.resolved_trends_mode == "OFFICIAL_API"

    def _guard(self) -> None:
        if not self.available:
            raise ProviderAccessUnavailable(
                "Google Trends OFFICIAL_API unavailable: alpha credentials/endpoint not configured. "
                "Use IMPORT (CSV export) until alpha access is granted.")

    async def get_geographic_interest(self, provider_id: str, *, region: str) -> list[DemandDatum]:
        self._require(CAP_GEOGRAPHIC)
        self._guard()
        raise ProviderAccessUnavailable("official geographic interest not implemented (no alpha docs)")

    async def get_historical_interest(self, provider_id: str, *, region: str) -> list[DemandDatum]:
        self._require(CAP_HISTORICAL)
        self._guard()
        raise ProviderAccessUnavailable("official historical interest not implemented (no alpha docs)")

    # ---- Phase 5A.3: official-API collection-mode readiness (gated) --------------------------
    # Trends data updates at most daily; there is NO value in intraday polling, and the scheduler must
    # not create an intraday Trends loop. These declare the intended collection shape for when alpha
    # access is granted; until then every call honestly reports ACCESS_UNAVAILABLE (never fabricated).
    supports_intraday: bool = False
    natural_cadence_seconds: int = 86400

    async def backfill_historical(self, provider_id: str, *, region: str = "IN",
                                  max_days: int | None = None, identity_type: str = "SEARCH_TERM"
                                  ) -> list[DemandDatum]:
        """One-time maximum-permitted historical daily window (India + sub-regions) for a resolved
        Trends identity. Gated: raises ACCESS_UNAVAILABLE until official alpha access exists."""
        self._require(CAP_HISTORICAL)
        self._guard()
        raise ProviderAccessUnavailable("official historical backfill unavailable (no alpha access)")

    async def incremental(self, provider_id: str, *, region: str = "IN",
                          since: str | None = None, identity_type: str = "SEARCH_TERM"
                          ) -> list[DemandDatum]:
        """Collect only newly-available periods at the natural (daily) Trends cadence — never intraday.
        Gated: raises ACCESS_UNAVAILABLE until official alpha access exists."""
        self._require(CAP_HISTORICAL)
        self._guard()
        raise ProviderAccessUnavailable("official incremental collection unavailable (no alpha access)")


def _parse_date(value: str):
    from datetime import UTC, datetime
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None
