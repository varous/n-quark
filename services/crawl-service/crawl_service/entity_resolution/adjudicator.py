"""Provider-neutral entity adjudication seam (Phase 5B.2.3).

AI-assisted adjudication is permitted ONLY for bounded, ambiguous cases the deterministic pipeline cannot
decide (is this one artist or several? artist/venue/organizer/non-entity? which existing candidate?). It is
EVIDENCE, never canonical authority: output is structured, confidence-scored, and must clear the existing
governance thresholds before anything canonical is created.

This module is only the seam + a Disabled implementation. No provider/credential is configured or approved
in this pass, so the runtime adjudicator is DISABLED — the deterministic pipeline + review queue fully
handle resolution, and ambiguous cases become REVIEW_REQUIRED rather than being silently AI-decided. A real
provider is dropped in later by implementing ``EntityAdjudicator`` and returning it from ``get_adjudicator``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# adjudication classifications (mirror the deterministic vocabulary)
ARTIST, VENUE, ORGANIZER, NON_ENTITY, UNKNOWN = "ARTIST", "VENUE", "ORGANIZER", "NON_ENTITY", "UNKNOWN"


@dataclass(frozen=True)
class Adjudication:
    """Structured, bounded adjudicator output — evidence, not a canonical decision."""
    classification: str                       # SINGLE_ENTITY|COMPOUND_ENTITY|<type>|NON_ENTITY|UNKNOWN
    entity_type: str | None = None            # ARTIST|VENUE|ORGANIZER|NON_ENTITY|UNKNOWN
    parts: list[str] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    needs_review: bool = True
    provider: str = "disabled"


@runtime_checkable
class EntityAdjudicator(Protocol):
    available: bool

    async def classify(self, *, raw: str, expected_role: str, context: dict[str, Any]) -> Adjudication | None: ...
    async def split(self, *, raw: str, context: dict[str, Any]) -> Adjudication | None: ...
    async def choose_candidate(self, *, raw: str, candidates: list[dict[str, Any]],
                               context: dict[str, Any]) -> Adjudication | None: ...


class DisabledAdjudicator:
    """The default: no model configured. Every call returns None so callers fall back to REVIEW_REQUIRED.
    Deterministic resolution is unaffected by the adjudicator being unavailable."""

    available = False
    provider = "disabled"

    async def classify(self, *, raw: str, expected_role: str, context: dict[str, Any]) -> Adjudication | None:
        return None

    async def split(self, *, raw: str, context: dict[str, Any]) -> Adjudication | None:
        return None

    async def choose_candidate(self, *, raw: str, candidates: list[dict[str, Any]],
                               context: dict[str, Any]) -> Adjudication | None:
        return None


def get_adjudicator(settings: Any | None = None) -> EntityAdjudicator:
    """Return the configured adjudicator, or the Disabled one when no provider is approved/configured.

    No AI subscription decision is made silently here: until a real provider + credential is wired and
    ``entity_adjudicator_enabled`` is set, this returns DisabledAdjudicator."""
    enabled = bool(getattr(settings, "entity_adjudicator_enabled", False)) if settings else False
    if not enabled:
        return DisabledAdjudicator()
    # A real provider implementation would be selected here (credential + provider name from settings),
    # e.g. an Anthropic-backed adjudicator. Intentionally not fabricated in this pass.
    return DisabledAdjudicator()
