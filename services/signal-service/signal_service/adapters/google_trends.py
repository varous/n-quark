"""Google Trends adapter — the geographic demand signal.

Trends is uniquely valuable for the India-first thesis: it is the only near-free signal
that reveals *which Indian states/cities* search for an artist — the input to regional
strength, venue fit, and tour routing. But its data is treacherous: a relative 0-100 index,
re-normalized on every pull, sampled with noise, and with no reliable free API.

So this adapter deliberately extracts only what Trends is trustworthy for:
  1. geographic *distribution* (robust within a single pull),
  2. momentum *direction* (rising / steady / falling / breakout),
  3. *discovery* via rising related queries,
and — like MusicBrainz's MBID — a canonical identity cross-reference: the Google Knowledge
Graph topic id (mID). It never stores the raw 0-100 as if it were a stable metric.

Access is pluggable because Trends has no single canonical path:
  - MockProvider (default, offline),
  - DataForSEOProvider (production reference — cheap, pay-as-you-go, batch),
  - SerpApiProvider (dev — free tier, fast, better KG parsing).
Live validation is deferred until a provider key is configured.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from signal_service.config import settings
from signal_service.schemas import GoogleTrendsSignals, NormalizedObservation

SIGNAL_SOURCE = "google_trends"
ADAPTER_VERSION = "google-trends-v1"

# DataForSEO wants a location name, not an ISO code.
_REGION_TO_LOCATION = {"IN": "India", "US": "United States", "GB": "United Kingdom"}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower().strip()).strip("-") or "unknown"


def entity_id_for_query(query: str) -> str:
    """Type-neutral source handle for a Trends query (an artist search topic)."""
    return f"google:query:{_slug(query)}"


@dataclass
class TrendsRaw:
    """Provider-agnostic normalized-input shape."""

    query: str
    region: str
    interest_by_region: dict[str, int] = field(default_factory=dict)
    timeseries: list[int] = field(default_factory=list)
    related_rising: list[str] = field(default_factory=list)
    kg_mid: str | None = None


class GoogleTrendsProvider(Protocol):
    name: str

    async def fetch(self, query: str, region: str) -> TrendsRaw: ...


# --------------------------------------------------------------------------- mock
_MOCK_CATALOG: dict[str, TrendsRaw] = {
    "arijit singh": TrendsRaw(
        query="Arijit Singh",
        region="IN",
        interest_by_region={
            "West Bengal": 100,
            "Tripura": 96,
            "Assam": 90,
            "Jharkhand": 78,
            "Maharashtra": 74,
            "Delhi": 69,
            "Karnataka": 58,
        },
        timeseries=[62, 60, 65, 63, 70, 72, 78, 85],
        related_rising=["arijit singh live 2026", "arijit singh concert tickets", "arijit singh tour"],
        kg_mid="/m/0j_gp3",
    ),
    "diljit dosanjh": TrendsRaw(
        query="Diljit Dosanjh",
        region="IN",
        interest_by_region={
            "Punjab": 100,
            "Chandigarh": 97,
            "Haryana": 82,
            "Delhi": 74,
            "Himachal Pradesh": 66,
            "Maharashtra": 51,
        },
        timeseries=[40, 44, 55, 70, 62, 80, 100, 96],
        related_rising=["diljit dil-luminati tour", "diljit dosanjh india tour"],
        kg_mid="/m/0h7c9dn",
    ),
}


class MockGoogleTrendsProvider:
    name = "mock"

    async def fetch(self, query: str, region: str) -> TrendsRaw:
        hit = _MOCK_CATALOG.get(query.strip().lower())
        if hit is not None:
            return hit
        return TrendsRaw(
            query=query,
            region=region,
            interest_by_region={"Maharashtra": 100, "Delhi": 84, "Karnataka": 61},
            timeseries=[50, 52, 49, 53, 55, 54, 58, 60],
            related_rising=[],
            kg_mid=None,
        )


# ---------------------------------------------------------------- dataforseo (prod)
class DataForSEOProvider:
    """Production reference. Untested live — validate once credentials are configured."""

    name = "dataforseo"

    async def fetch(self, query: str, region: str) -> TrendsRaw:
        location = _REGION_TO_LOCATION.get(region, "India")
        body = [
            {
                "keywords": [query],
                "location_name": location,
                "language_name": "English",
                "type": "web",
                "item_types": [
                    "google_trends_graph",
                    "google_trends_map",
                    "google_trends_queries_list",
                ],
            }
        ]
        async with httpx.AsyncClient(
            timeout=30.0, auth=(settings.dataforseo_login, settings.dataforseo_password)
        ) as client:
            response = await client.post(
                f"{settings.dataforseo_api_base}/keywords_data/google_trends/explore/live",
                json=body,
            )
            response.raise_for_status()
            payload = response.json()

        items = (((payload.get("tasks") or [{}])[0].get("result") or [{}])[0] or {}).get("items") or []
        raw = TrendsRaw(query=query, region=region)
        for item in items:
            itype = item.get("type")
            data = item.get("data") or []
            if itype == "google_trends_map":
                raw.interest_by_region = {
                    d.get("geo_name", "?"): int((d.get("values") or [0])[0] or 0) for d in data
                }
            elif itype == "google_trends_graph":
                raw.timeseries = [int((d.get("values") or [0])[0] or 0) for d in data]
            elif itype == "google_trends_queries_list":
                rising = (item.get("keyword_data") or {}).get("rising") or []
                raw.related_rising = [r.get("query", "") for r in rising if r.get("query")]
        return raw


# ------------------------------------------------------------------- serpapi (dev)
class SerpApiProvider:
    """Dev option (free tier, fast). GEO_MAP_0 is the single-query interest-by-region map
    (GEO_MAP is the multi-term compared variant and 400s for one query)."""

    name = "serpapi"

    def _scrub(self, text: str) -> str:
        """Never let the api_key surface in an error message or log line."""
        return text.replace(settings.serpapi_key, "***") if settings.serpapi_key else text

    async def _get(self, client: httpx.AsyncClient, query: str, region: str, data_type: str) -> dict[str, Any]:
        try:
            response = await client.get(
                settings.serpapi_api_base,
                params={
                    "engine": "google_trends",
                    "q": query,
                    "geo": region,
                    "data_type": data_type,
                    "api_key": settings.serpapi_key,
                },
            )
        except httpx.HTTPError as exc:
            # `from None` drops the chained cause so the key-bearing URL can't leak via __cause__.
            raise ValueError(f"SerpApi {data_type} request failed: {self._scrub(str(exc))}") from None

        if response.status_code >= 400:
            try:
                detail = response.json().get("error") or response.text[:200]
            except ValueError:
                detail = f"HTTP {response.status_code}"
            raise ValueError(f"SerpApi {data_type} error: {self._scrub(str(detail))}")
        return response.json()

    async def fetch(self, query: str, region: str) -> TrendsRaw:
        raw = TrendsRaw(query=query, region=region)
        async with httpx.AsyncClient(timeout=20.0) as client:
            geo = await self._get(client, query, region, "GEO_MAP_0")
            raw.interest_by_region = {
                r.get("location", "?"): int(r.get("value") or r.get("extracted_value") or 0)
                for r in (geo.get("interest_by_region") or [])
            }
            series = await self._get(client, query, region, "TIMESERIES")
            raw.timeseries = [
                int((point.get("values") or [{}])[0].get("extracted_value") or 0)
                for point in ((series.get("interest_over_time") or {}).get("timeline_data") or [])
            ]
            related = await self._get(client, query, region, "RELATED_QUERIES")
            raw.related_rising = [
                r.get("query", "")
                for r in ((related.get("related_queries") or {}).get("rising") or [])
                if r.get("query")
            ]
        return raw


def get_provider() -> GoogleTrendsProvider:
    provider = settings.resolved_trends_provider
    if provider == "dataforseo":
        return DataForSEOProvider()
    if provider == "serpapi":
        return SerpApiProvider()
    return MockGoogleTrendsProvider()


# ------------------------------------------------------------------- normalization
def derive_momentum(timeseries: list[int]) -> tuple[str, bool]:
    """Direction, not level. Returns (category, is_breakout). Level is too noisy to trust."""
    if len(timeseries) < 4:
        return "unknown", False
    mid = len(timeseries) // 2
    earlier = sum(timeseries[:mid]) / max(mid, 1)
    recent = sum(timeseries[mid:]) / max(len(timeseries) - mid, 1)
    breakout = earlier > 0 and timeseries[-1] >= earlier * 3
    if earlier <= 0:
        return ("rising" if recent > 0 else "unknown"), breakout
    ratio = recent / earlier
    if ratio >= 1.15:
        return "rising", breakout
    if ratio <= 0.85:
        return "falling", breakout
    return "steady", breakout


def _provenance(when: datetime, query: str, region: str) -> dict[str, Any]:
    return {
        "acquisition_method": "aggregator_api",
        "legal_basis": "aggregator_contract",
        "data_subject_type": "entity",
        "contains_pii": False,
        "adapter_version": ADAPTER_VERSION,
        "collected_at": when.isoformat(),
        "source_url": f"https://trends.google.com/trends/explore?geo={region}&q={query}",
    }


def normalize_trends(
    raw: TrendsRaw,
    provider_name: str,
    *,
    fetched_at: datetime | None = None,
) -> GoogleTrendsSignals:
    """Convert provider output into observations, keeping only Trends' trustworthy signals."""
    when = fetched_at or datetime.now(UTC)
    entity = entity_id_for_query(raw.query)
    mock = provider_name == "mock"
    base_evidence = {"query": raw.query, "region": raw.region, "provider": provider_name}
    base_metadata = {
        "adapter": ADAPTER_VERSION,
        "signal_provider": SIGNAL_SOURCE,
        "mock": mock,
        "provenance": _provenance(when, raw.query, raw.region),
    }

    def obs(attribute: str, value: Any, confidence: float, evidence: dict[str, Any] | None = None) -> NormalizedObservation:
        return NormalizedObservation(
            entity=entity,
            attribute=attribute,
            value=value,
            source=SIGNAL_SOURCE,
            timestamp=when,
            confidence=confidence if not mock else min(confidence, 0.5),
            evidence={**base_evidence, **(evidence or {})},
            metadata=base_metadata,
        )

    observations: list[NormalizedObservation] = []

    if raw.interest_by_region:
        observations.append(
            obs("search_interest_by_region", raw.interest_by_region, 0.7, {"scale": "0-100 within-pull"})
        )
        top = sorted(raw.interest_by_region.items(), key=lambda kv: kv[1], reverse=True)[:5]
        observations.append(obs("search_top_regions", [name for name, _ in top], 0.7))

    if raw.timeseries:
        momentum, breakout = derive_momentum(raw.timeseries)
        observations.append(
            obs("search_momentum", momentum, 0.6, {"breakout": breakout, "recent_series": raw.timeseries[-8:]})
        )

    if raw.related_rising:
        observations.append(obs("related_rising_queries", raw.related_rising, 0.6))

    if raw.kg_mid:
        # Identity cross-reference — like MusicBrainz's MBID, feeds the entity backbone.
        observations.append(obs("google_kg_mid", raw.kg_mid, 0.7, {"id_scheme": "google_knowledge_graph"}))

    return GoogleTrendsSignals(
        query=raw.query,
        entity=entity,
        region=raw.region,
        provider=provider_name,
        observations=observations,
        fetched_at=when,
        mock=mock,
    )


class GoogleTrendsClient:
    async def fetch_query(self, query: str, region: str | None = None) -> GoogleTrendsSignals:
        provider = get_provider()
        raw = await provider.fetch(query, region or settings.google_trends_region)
        return normalize_trends(raw, provider.name)
