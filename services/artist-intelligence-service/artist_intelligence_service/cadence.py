"""Deterministic adaptive collection cadence (Phase 5A.3) — pure functions, config-driven.

Not every artist/video is refreshed equally: fresh videos and imminent-event artists earn higher
temporal resolution; long-tail content is sampled sparsely. Everything here is a transparent function of
observed age / event proximity + configurable cadence bands (no scores, no hidden heuristics). The
scheduler applies the result to ``next_refresh_at`` and stays bounded by quota availability.
"""

from __future__ import annotations

from artist_intelligence_service.config import settings

# Acquisition priority classes (lower = more urgent). Operational only — NOT an artist-value score.
P0_UPCOMING_EVENT = 0        # upcoming observed Indian event
P1_MULTI_REFERENCE = 10      # multiple independent Indian live-market references
P2_CONFIRMED_OR_STRONG = 20  # confirmed prior Indian event / strong India demand
P3_YT_DISCOVERY = 30         # India-linked YouTube discovery
P4_GLOBAL_CANDIDATE = 40     # relevant candidate without confirmed India evidence


def channel_cadence_seconds(*, days_to_event: float | None, recently_active: bool) -> int:
    """How often to refresh a channel snapshot, by event proximity then activity."""
    if days_to_event is not None and days_to_event >= -3:  # event imminent or just passed
        return settings.cadence_channel_event_imminent_s
    if recently_active:
        return settings.cadence_channel_active_s
    return settings.cadence_channel_standard_s


def channel_longtail_seconds() -> int:
    return settings.cadence_channel_longtail_s


def video_cadence_seconds(age_days: float) -> int:
    """Age-based video refresh cadence: newer uploads change fastest, so observe them fastest."""
    if age_days <= 3:
        return settings.cadence_video_fresh_s
    if age_days <= 14:
        return settings.cadence_video_recent_s
    if age_days <= 90:
        return settings.cadence_video_mature_s
    return settings.cadence_video_old_s


def event_band_seconds(days_to_event: float | None) -> int | None:
    """Event-aware artist cadence around an Indian event (T-relative). None ⇒ no event influence."""
    if days_to_event is None:
        return None
    d = days_to_event
    if d > 60 or d < -3:
        return None
    if d > 30:
        return settings.cadence_event_t60_s     # T-60 … T-30
    if d > 14:
        return settings.cadence_event_t30_s     # T-30 … T-14
    if d > 3:
        return settings.cadence_event_t14_s     # T-14 … T-3
    if d >= 0:
        return settings.cadence_event_t3_s      # T-3 … T
    return settings.cadence_event_post_s        # T … T+3


def priority_for(*, days_to_event: float | None, independent_references: int,
                 has_confirmed_or_strong: bool, from_youtube_discovery: bool) -> int:
    """Deterministic acquisition priority class from operational factors (not popularity)."""
    if days_to_event is not None and 0 <= days_to_event <= 60:
        return P0_UPCOMING_EVENT
    if independent_references >= 2:
        return P1_MULTI_REFERENCE
    if has_confirmed_or_strong:
        return P2_CONFIRMED_OR_STRONG
    if from_youtube_discovery:
        return P3_YT_DISCOVERY
    return P4_GLOBAL_CANDIDATE
