"""Deterministic entity classification — decides what KIND of thing a source node is.

Runs ahead of canonical resolution so a label (e.g. T-Series) is never mistaken for an
artist. A YouTube channel id identifies a thing but says nothing about its kind, so the
adapter must not assert a type; this step infers it.

Deterministic-first (per the product philosophy):
  1. MusicBrainz cross-reference — artist vs label ground truth (highest precision);
  2. name / structure heuristics — when MusicBrainz has no confident match.
An AI classifier would slot in as a third tier, emitting its guess as a low-confidence
observation. The result is a *candidate*: type + confidence + reasons. Low confidence is a
signal to route to review, not to trust blindly.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from signal_service.adapters.musicbrainz import MusicBrainzClient, _confidence_from_score
from signal_service.schemas import NormalizedObservation

CLASSIFIER_VERSION = "entity-classifier-v2"

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
    method: str  # "musicbrainz" | "heuristic" | "default"
    reasons: list[str] = field(default_factory=list)
    needs_review: bool = False
    mbid: str | None = None


def classify_by_heuristics(name: str, raw: dict | None = None) -> Classification:
    """Fallback classifier: name / structure heuristics. Never assumes 'artist' blindly."""
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

    # No label signal — provisionally an artist, but low confidence and flagged for review.
    return Classification(
        "artist",
        0.5,
        "default",
        ["no MusicBrainz match and no label signal; provisionally artist pending confirmation"],
        needs_review=True,
    )


async def classify_channel(
    name: str,
    raw: dict | None = None,
    mb_client: MusicBrainzClient | None = None,
) -> Classification:
    """Infer entity type: MusicBrainz first, heuristics as fallback. Never assumes artist."""
    if mb_client is not None:
        try:
            match = await mb_client.classify_name(name)
        except Exception:  # noqa: BLE001 — a MusicBrainz outage must fall back, not fail
            match = None
        if match is not None:
            confidence = _confidence_from_score(match.score)
            return Classification(
                match.entity_type,
                confidence,
                "musicbrainz",
                [
                    (
                        f"musicbrainz: '{match.name}' is a {match.entity_type} "
                        f"(score {match.score}, mbid {match.mbid})"
                    )
                ],
                needs_review=confidence < 0.7,
                mbid=match.mbid,
            )

    return classify_by_heuristics(name, raw)


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
            "mbid": classification.mbid,
        },
        metadata={
            "classifier_version": CLASSIFIER_VERSION,
            "derived": True,
        },
    )
