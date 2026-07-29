"""MusicBrainz adapter — the canonical artist/label cross-reference.

Two jobs:
  1. Disambiguate what KIND of entity a name is (artist vs label). MusicBrainz models
     these as separate entity types, so a name like "T-Series" resolves as a *label*,
     not an artist — the deterministic ground truth the classifier needs.
  2. Provide the canonical MusicBrainz id (MBID) as an entity-backbone enrichment.

MusicBrainz is open (no API key) but rate-limited to ~1 request/second and requires a
descriptive User-Agent. Mock mode gives deterministic offline output for tests/demos.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

import httpx

from signal_service.config import settings
from signal_service.schemas import NormalizedObservation

SIGNAL_SOURCE = "musicbrainz"
ADAPTER_VERSION = "musicbrainz-v1"

# Below this MusicBrainz search score we don't trust the match and defer to heuristics.
MIN_TRUST_SCORE = 85


@dataclass
class MusicBrainzMatch:
    entity_type: str  # "artist" | "label"
    mbid: str
    score: int
    name: str


# Deterministic offline catalog, keyed by lowercased name.
_MOCK_CATALOG: dict[str, MusicBrainzMatch] = {
    "t-series": MusicBrainzMatch(
        "label", "c9f5b9c5-0000-4000-8000-t-series0001", 100, "T-Series"
    ),
    "arijit singh": MusicBrainzMatch(
        "artist", "b7a9f0e2-0000-4000-8000-arijit00001", 100, "Arijit Singh"
    ),
    "sony music entertainment": MusicBrainzMatch(
        "label", "a1b2c3d4-0000-4000-8000-sony000001", 100, "Sony Music Entertainment"
    ),
}


def _confidence_from_score(score: int) -> float:
    if score >= 95:
        return 0.97
    if score >= 85:
        return 0.9
    if score >= 70:
        return 0.8
    return 0.6


class MusicBrainzClient:
    # process-wide cache so repeat channels don't re-hit the rate-limited API
    _cache: ClassVar[dict[str, MusicBrainzMatch | None]] = {}

    async def classify_name(self, name: str) -> MusicBrainzMatch | None:
        """Return the best artist/label match for a name, or None if not confident."""
        key = name.strip().lower()
        if key in self._cache:
            return self._cache[key]

        if settings.use_musicbrainz_mock:
            match = _MOCK_CATALOG.get(key)
            self._cache[key] = match
            return match

        label = await self._search("label", name)
        await asyncio.sleep(1.0)  # honor MusicBrainz ~1 req/s rate limit
        artist = await self._search("artist", name)

        candidates = [c for c in (label, artist) if c is not None]
        match: MusicBrainzMatch | None = None
        if candidates:
            best = max(candidates, key=lambda c: c.score)
            if best.score >= MIN_TRUST_SCORE:
                match = best

        self._cache[key] = match
        return match

    async def _search(self, entity: str, name: str) -> MusicBrainzMatch | None:
        params = {"query": name, "fmt": "json", "limit": 3}
        headers = {"User-Agent": settings.musicbrainz_user_agent}
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{settings.musicbrainz_api_base}/{entity}",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()

        results = payload.get(f"{entity}s") or []
        if not results:
            return None
        top = results[0]
        try:
            score = int(top.get("score", 0) or 0)
        except (TypeError, ValueError):
            score = 0
        return MusicBrainzMatch(entity, top.get("id", ""), score, top.get("name", name))


def musicbrainz_observation(
    entity: str,
    match: MusicBrainzMatch,
    *,
    when: datetime | None = None,
) -> NormalizedObservation:
    """Enrichment: attach the canonical MusicBrainz id to the entity (backbone id)."""
    now = when or datetime.now(UTC)
    return NormalizedObservation(
        entity=entity,
        attribute="musicbrainz_id",
        value=match.mbid,
        source=SIGNAL_SOURCE,
        timestamp=now,
        confidence=_confidence_from_score(match.score),
        evidence={
            "musicbrainz_type": match.entity_type,
            "matched_name": match.name,
            "search_score": match.score,
        },
        metadata={
            "adapter": ADAPTER_VERSION,
            "signal_provider": SIGNAL_SOURCE,
            "provenance": {
                "acquisition_method": "official_api",
                "legal_basis": "platform_api_tos",
                "data_subject_type": "entity",
                "contains_pii": False,
                "adapter_version": ADAPTER_VERSION,
                "collected_at": now.isoformat(),
                "source_url": f"https://musicbrainz.org/{match.entity_type}/{match.mbid}",
            },
        },
    )
