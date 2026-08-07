"""Provider-neutral demand-intelligence contract (Phase 5A).

Providers differ in what they can do, so they advertise *capability flags* instead of being forced to
implement unsupported operations. The service checks the flag before calling; an unsupported call
raises ``CapabilityUnsupported`` rather than returning a fake value.

Providers return normalized value objects (``DemandDatum`` / ``ArtistCandidate``); the service persists
them with full provenance. YouTube acquisition is delegated to signal-service, so the YouTube provider
here is a thin client over signal-service — not a second YouTube HTTP client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# ---- providers --------------------------------------------------------------------------------
PROVIDER_YOUTUBE = "YOUTUBE"
PROVIDER_GOOGLE_TRENDS = "GOOGLE_TRENDS"

# ---- capability flags -------------------------------------------------------------------------
CAP_SEARCH = "search_artist"
CAP_RESOLVE = "resolve_identity"
CAP_METADATA = "get_artist_metadata"
CAP_GLOBAL_SNAPSHOT = "get_global_snapshot"
CAP_CONTENT_SNAPSHOT = "get_content_snapshot"
CAP_GEOGRAPHIC = "get_geographic_interest"
CAP_HISTORICAL = "get_historical_interest"
CAP_IMPORT = "import_export"

# ---- identity resolution outcomes -------------------------------------------------------------
RESOLVED = "RESOLVED"
AMBIGUOUS = "AMBIGUOUS"
UNRESOLVED = "UNRESOLVED"

# ---- metric names (namespaced by provider) ----------------------------------------------------
YT_CHANNEL_VIEWS = "YOUTUBE_CHANNEL_VIEWS"
YT_SUBSCRIBERS = "YOUTUBE_SUBSCRIBERS"
YT_VIDEO_COUNT = "YOUTUBE_VIDEO_COUNT"
YT_VIDEO_VIEWS = "YOUTUBE_VIDEO_VIEWS"
YT_VIDEO_LIKES = "YOUTUBE_VIDEO_LIKES"
YT_VIDEO_COMMENTS = "YOUTUBE_VIDEO_COMMENTS"
GOOGLE_SEARCH_INTEREST = "GOOGLE_SEARCH_INTEREST"

# ---- evidence statuses ------------------------------------------------------------------------
DIRECT_PROVIDER_VALUE = "DIRECT_PROVIDER_VALUE"
PROVIDER_REPORTED = "PROVIDER_REPORTED"
PROVIDER_SAMPLED = "PROVIDER_SAMPLED"
PROVIDER_NORMALIZED = "PROVIDER_NORMALIZED"
IMPORTED_PROVIDER_EXPORT = "IMPORTED_PROVIDER_EXPORT"
DERIVED = "DERIVED"
SEARCH_TERM_BASED = "SEARCH_TERM_BASED"
TOPIC_BASED = "TOPIC_BASED"
UNKNOWN = "UNKNOWN"


class CapabilityUnsupported(RuntimeError):
    """Raised when a provider is asked for an operation it does not advertise."""


class ProviderAccessUnavailable(RuntimeError):
    """Raised when a provider is configured but its access/credentials are unavailable.

    The caller surfaces this as ``ACCESS_UNAVAILABLE`` rather than fabricating data."""


@dataclass
class ArtistCandidate:
    """A ranked identity candidate from a bounded search — evidence, not a decision."""

    provider_id: str
    identity_type: str                 # CHANNEL_ID | HANDLE | SEARCH_TERM | TOPIC_ID
    display_name: str
    canonical_url: str | None = None
    score: float = 0.0
    signals: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)


@dataclass
class IdentityResolution:
    """The deterministic outcome of resolving a canonical artist to a provider identity."""

    status: str                        # RESOLVED | AMBIGUOUS | UNRESOLVED
    method: str
    reason: str
    chosen: ArtistCandidate | None = None
    candidates: list[ArtistCandidate] = field(default_factory=list)


@dataclass
class DemandDatum:
    """One normalized demand fact ready to persist as an ``artist_demand_observation``.

    ``dedup_extra`` is folded into the observation_key so distinct facts of the same
    (artist, provider, metric, scope, timestamp) — e.g. two different videos — never collide."""

    metric: str
    value_numeric: float | None = None
    value_text: str | None = None
    unit: str | None = None
    scope_type: str = "GLOBAL"
    scope_id: str | None = None
    scope_label: str | None = None
    provider_timestamp: datetime | None = None
    evidence_status: str = UNKNOWN
    dedup_extra: str = ""
    provenance: dict = field(default_factory=dict)


class ArtistIntelligenceProvider:
    """Base provider. Subclasses set ``name`` + ``capabilities`` and override supported operations.

    The default implementations refuse cleanly, so callers can rely on capability flags being honest.
    """

    name: str = "base"
    capabilities: frozenset[str] = frozenset()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def _require(self, capability: str) -> None:
        if not self.supports(capability):
            raise CapabilityUnsupported(f"{self.name} does not support {capability}")

    async def search_artist(self, query: str, *, limit: int) -> list[ArtistCandidate]:
        self._require(CAP_SEARCH)
        raise NotImplementedError

    async def resolve_identity(self, query: str, *, hints: dict, limit: int) -> IdentityResolution:
        self._require(CAP_RESOLVE)
        raise NotImplementedError

    async def get_artist_metadata(self, provider_id: str) -> dict:
        self._require(CAP_METADATA)
        raise NotImplementedError

    async def get_global_snapshot(self, provider_id: str) -> list[DemandDatum]:
        self._require(CAP_GLOBAL_SNAPSHOT)
        raise NotImplementedError

    async def get_content_snapshot(self, provider_id: str, *, limit: int) -> list[DemandDatum]:
        self._require(CAP_CONTENT_SNAPSHOT)
        raise NotImplementedError

    async def get_geographic_interest(self, provider_id: str, *, region: str) -> list[DemandDatum]:
        self._require(CAP_GEOGRAPHIC)
        raise NotImplementedError

    async def get_historical_interest(self, provider_id: str, *, region: str) -> list[DemandDatum]:
        self._require(CAP_HISTORICAL)
        raise NotImplementedError
