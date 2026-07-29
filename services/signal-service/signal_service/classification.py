"""Deterministic entity classification — decides what KIND of thing a source node is.

Runs ahead of canonical resolution so a label (e.g. T-Series) is never mistaken for an
artist. A YouTube channel id identifies a thing but says nothing about its kind, so the
adapter must not assert a type; this step infers it.

Deterministic-first (per the product philosophy):
  1. a known-entity registry — stands in for a MusicBrainz artist/label cross-reference
     until the MusicBrainz adapter lands (roadmap #3);
  2. name / structure heuristics.
An AI fallback would slot in as step 3, emitting its guess as a low-confidence observation.

The result is a *candidate*: type + confidence + the reasons behind it. Low confidence is a
signal to route to review, not to trust blindly.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from signal_service.schemas import NormalizedObservation

CLASSIFIER_VERSION = "entity-classifier-v1"

# Stand-in for MusicBrainz artist/label disambiguation, keyed by YouTube channel id.
# In MusicBrainz, T-Series resolves to a *label*, not an artist.
KNOWN_CHANNEL_TYPES: dict[str, tuple[str, str]] = {
    "UCq-Fj5jknLsUf-MWSy4_brA": (
        "label",
        "musicbrainz: 'T-Series' is a label (Super Cassettes Industries), not an artist",
    ),
}

# Tokens that signal an organization/label rather than a performing artist.
LABEL_NAME_TOKENS = (
    "records",
    "music",
    "entertainment",
    "films",
    "series",
    "studios",
    "media",
    "productions",
    "official",
    "tv",
)

# Video counts above this suggest an aggregator/label channel, not a single artist.
AGGREGATOR_VIDEO_THRESHOLD = 2000


@dataclass
class Classification:
    entity_type: str
    confidence: float
    method: str  # "registry" | "heuristic" | "default"
    reasons: list[str] = field(default_factory=list)
    needs_review: bool = False


def classify_youtube_channel(
    channel_id: str,
    name: str,
    raw: dict | None = None,
) -> Classification:
    """Infer the entity type behind a YouTube channel. Never assumes 'artist'."""
    # 1. Deterministic registry (MusicBrainz stand-in) — highest precision.
    known = KNOWN_CHANNEL_TYPES.get(channel_id)
    if known is not None:
        entity_type, reason = known
        return Classification(entity_type, 0.97, "registry", [reason])

    # 2. Name / structure heuristics.
    lowered = name.lower()
    token_hits = [token for token in LABEL_NAME_TOKENS if token in lowered]
    statistics = (raw or {}).get("statistics") or {}
    try:
        video_count = int(statistics.get("videoCount", 0) or 0)
    except (TypeError, ValueError):
        video_count = 0

    if token_hits:
        confidence = 0.75 if video_count >= AGGREGATOR_VIDEO_THRESHOLD else 0.6
        return Classification(
            "label",
            confidence,
            "heuristic",
            [
                f"name contains label token(s): {', '.join(token_hits)}",
                f"videoCount={video_count}",
            ],
            needs_review=confidence < 0.7,
        )

    # 3. No label signal — provisionally an artist, but low confidence and flagged.
    #    We do NOT silently trust this; an AI classifier / human review would confirm.
    return Classification(
        "artist",
        0.5,
        "default",
        ["no label signal detected; provisionally artist pending confirmation"],
        needs_review=True,
    )


def classification_observation(
    entity: str,
    classification: Classification,
    *,
    when: datetime | None = None,
) -> NormalizedObservation:
    """The classification is itself an observation — a derived inference with confidence.

    Internally derived (not fetched from an external platform), so it carries no external
    provenance block; its evidence records the method and reasoning instead.
    """
    return NormalizedObservation(
        entity=entity,
        attribute="candidate_entity_type",
        value=classification.entity_type,
        source="entity-classifier",
        timestamp=when or datetime.now(UTC),
        confidence=classification.confidence,
        evidence={
            "method": classification.method,
            "reasons": classification.reasons,
            "needs_review": classification.needs_review,
        },
        metadata={
            "classifier_version": CLASSIFIER_VERSION,
            "derived": True,
        },
    )
