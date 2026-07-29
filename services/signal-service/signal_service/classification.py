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

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from signal_service.adapters.musicbrainz import (
    MusicBrainzClient,
    MusicBrainzLookup,
    MusicBrainzMatch,
    _confidence_from_score,
)
from signal_service.schemas import NormalizedObservation

logger = logging.getLogger(__name__)

CLASSIFIER_VERSION = "entity-classifier-v3"

# MusicBrainz trust + tie-break thresholds.
MB_MIN_TRUST_SCORE = 85
MB_TIE_MARGIN = 8

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


# Unicode dash variants MusicBrainz uses (e.g. "T‐Series" with U+2010) folded to ASCII "-".
_DASHES = str.maketrans({d: "-" for d in "‐‑‒–—−"})


def _norm_name(value: str) -> str:
    return " ".join(value.translate(_DASHES).lower().split())


def _same_name(a: str, b: str) -> bool:
    return _norm_name(a) == _norm_name(b)


def _video_count(raw: dict | None) -> int:
    try:
        return int(((raw or {}).get("statistics") or {}).get("videoCount", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _from_match(match: MusicBrainzMatch, reason: str) -> Classification:
    confidence = _confidence_from_score(match.score)
    return Classification(
        match.entity_type,
        confidence,
        "musicbrainz",
        [reason],
        needs_review=confidence < 0.7,
        mbid=match.mbid,
    )


def decide_from_musicbrainz(
    lookup: MusicBrainzLookup,
    name: str,
    raw: dict | None,
) -> Classification | None:
    """Reconcile MusicBrainz label vs artist matches. Returns None to defer to heuristics.

    A name can match both types (a label and an unrelated same-named artist), so a clear
    score winner is trusted, but a genuine tie is broken by corroborating signals — exact
    name match and the channel's aggregator shape — and always flagged for review.
    """
    label = lookup.label if lookup.label and lookup.label.score >= MB_MIN_TRUST_SCORE else None
    artist = lookup.artist if lookup.artist and lookup.artist.score >= MB_MIN_TRUST_SCORE else None

    if label is None and artist is None:
        return None
    if artist is None:
        return _from_match(label, f"musicbrainz: only a label match ('{label.name}', score {label.score})")
    if label is None:
        return _from_match(artist, f"musicbrainz: only an artist match ('{artist.name}', score {artist.score})")

    if abs(label.score - artist.score) >= MB_TIE_MARGIN:
        best = label if label.score > artist.score else artist
        other = artist if best is label else label
        return _from_match(
            best,
            f"musicbrainz: {best.entity_type} score {best.score} beats {other.entity_type} {other.score}",
        )

    # Genuine tie — score alone can't decide (e.g. label vs coincidental same-named artist).
    reasons = [f"musicbrainz ambiguous: label {label.score} vs artist {artist.score}"]
    votes = {"label": 0, "artist": 0}

    if _same_name(label.name, name) and not _same_name(artist.name, name):
        votes["label"] += 1
        reasons.append(f"exact name match favors label ('{label.name}')")
    elif _same_name(artist.name, name) and not _same_name(label.name, name):
        votes["artist"] += 1
        reasons.append(f"exact name match favors artist ('{artist.name}')")

    video_count = _video_count(raw)
    if video_count >= AGGREGATOR_VIDEO_THRESHOLD:
        votes["label"] += 1
        reasons.append(f"aggregator channel (videoCount={video_count}) favors label")

    if votes["label"] != votes["artist"]:
        winner = "label" if votes["label"] > votes["artist"] else "artist"
        match = label if winner == "label" else artist
        return Classification(winner, 0.72, "musicbrainz+tiebreak", reasons, needs_review=True, mbid=match.mbid)

    # No corroborating signal — surface the ambiguity rather than guessing.
    best = label if label.score >= artist.score else artist
    return Classification(
        best.entity_type,
        0.6,
        "musicbrainz+tiebreak",
        [*reasons, "no corroborating signal; review required"],
        needs_review=True,
        mbid=best.mbid,
    )


async def classify_channel(
    name: str,
    raw: dict | None = None,
    mb_client: MusicBrainzClient | None = None,
) -> Classification:
    """Infer entity type: MusicBrainz first, heuristics as fallback. Never assumes artist."""
    if mb_client is not None:
        try:
            lookup = await mb_client.lookup(name)
        except Exception as exc:  # noqa: BLE001 — a MusicBrainz outage must fall back, not fail
            logger.warning("MusicBrainz lookup failed for %r; using heuristics: %s", name, exc)
            lookup = None
        if lookup is not None:
            decision = decide_from_musicbrainz(lookup, name, raw)
            if decision is not None:
                return decision

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
