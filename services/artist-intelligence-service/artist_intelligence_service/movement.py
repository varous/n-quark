"""Deterministic YouTube content-movement derivation (Phase 5B.2).

Movement is OBSERVED abnormal behaviour — never a prediction, never a single fused "virality score".
Every classification is:

- **age-normalised** — a video's current velocity is compared against the artist's OTHER videos of a
  comparable age (never against a mature video's lifetime totals);
- **evidence-gated** — velocity needs enough observations spanning enough time; a relative ratio needs a
  big enough comparable-age cohort; otherwise the honest state is INSUFFICIENT_HISTORY;
- **explainable** — each result carries the cohort compared, the observation + baseline sample sizes, the
  metrics + thresholds used, and the supporting values.

All thresholds live in config (transparent + tunable). This module only reads the existing per-video
observation time-series (``artist_demand_observation`` scope_type=CONTENT); it collects nothing.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from artist_intelligence_service import reads
from artist_intelligence_service import videos as vids
from artist_intelligence_service.config import settings
from artist_intelligence_service.models import YouTubeVideo
from artist_intelligence_service.providers.base import (
    YT_VIDEO_COMMENTS,
    YT_VIDEO_VIEWS,
)

# movement states (deterministic, explainable — NOT a score)
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
NORMAL = "NORMAL"
RISING = "RISING"
BREAKOUT_CANDIDATE = "BREAKOUT_CANDIDATE"
COOLING = "COOLING"

Series = list[tuple[datetime, float]]


def _now() -> datetime:
    return datetime.now(UTC)


def _age_buckets() -> list[float]:
    return [float(x) for x in str(settings.movement_age_buckets_hours).split(",") if x.strip()]


def _age_bucket_label(age_hours: float) -> str:
    bounds = _age_buckets()
    labels = ["<24h", "1-3d", "3-7d", "7-30d", ">30d"]
    for i, b in enumerate(bounds):
        if age_hours < b:
            return labels[i] if i < len(labels) else f"<{b:g}h"
    return labels[len(bounds)] if len(bounds) < len(labels) else f">{bounds[-1]:g}h"


def _hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600.0


def window_velocity(series: Series, now: datetime, *, window_hours: float,
                    offset_windows: int = 0) -> tuple[float, int, float] | None:
    """Views-per-hour over a window ending ``offset_windows`` windows before now.

    offset_windows=0 → the most recent window [now-w, now]; =1 → the window before it. Returns
    (velocity_per_hour, n_points, span_hours) or None when the window lacks ≥2 points spanning at least
    ``movement_min_time_separation_hours`` (so a single reading can never yield a velocity)."""
    end = now.timestamp() - offset_windows * window_hours * 3600
    start = end - window_hours * 3600
    pts = [(dt, v) for dt, v in series if start <= dt.timestamp() <= end]
    if len(pts) < 2:
        return None
    span = _hours(pts[0][0], pts[-1][0])
    if span < settings.movement_min_time_separation_hours:
        return None
    return ((pts[-1][1] - pts[0][1]) / span, len(pts), span)


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def build_baselines(db: Session, artist: str, videos: list[YouTubeVideo], now: datetime,
                    views_by_video: dict[str, Series]) -> dict[str, list[tuple[str, float]]]:
    """Comparable-age cohort: current velocity of every owned video as (video_id, velocity), grouped by
    age bucket. A video is compared only against same-age-bucket siblings (self excluded by id at use)."""
    buckets: dict[str, list[tuple[str, float]]] = {}
    for v in videos:
        if v.published_at is None:
            continue
        wv = window_velocity(views_by_video.get(v.video_id, []), now,
                             window_hours=settings.movement_recent_window_hours)
        if wv is None:
            continue
        buckets.setdefault(_age_bucket_label(_hours(_aware(v.published_at), now)), []).append(
            (v.video_id, wv[0]))
    return buckets


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def video_movement(video: YouTubeVideo, *, views: Series, comments: Series | None,
                   baseline_values: list[float], bucket_label: str | None, now: datetime) -> dict[str, Any]:
    """Classify one video's movement with a full evidence contract. ``baseline_values`` is the comparable-
    age cohort's current velocities (already excluding this video); ``bucket_label`` names that cohort."""
    w = settings.movement_recent_window_hours
    calc = {"video_id": video.video_id, "title": video.title,
            "relationship_type": video.relationship_type, "channel_id": video.channel_id,
            "calculated_at": now.isoformat(), "metrics_used": [YT_VIDEO_VIEWS],
            "thresholds": {"rising_ratio": settings.movement_rising_ratio,
                           "breakout_ratio": settings.movement_breakout_ratio,
                           "breakout_accel_ratio": settings.movement_breakout_accel_ratio,
                           "cooling_ratio": settings.movement_cooling_ratio,
                           "window_hours": w, "min_baseline_sample": settings.movement_min_baseline_sample}}
    n_obs = len(views)
    calc["observation_count"] = n_obs

    if video.published_at is None:
        age = None
    else:
        age = _hours(_aware(video.published_at), now)
    calc["supporting_values"] = {"age_hours": round(age, 2) if age is not None else None}

    # evidence sufficiency: enough observations spanning enough time to derive any velocity at all.
    span = _hours(views[0][0], views[-1][0]) if n_obs >= 2 else 0.0
    if n_obs < settings.movement_min_observations or span < settings.movement_min_time_separation_hours:
        return {**calc, "classification": INSUFFICIENT_HISTORY, "evidence_state": INSUFFICIENT_HISTORY,
                "comparison_cohort": None, "baseline_sample_size": 0,
                "reason": f"needs ≥{settings.movement_min_observations} observations spanning "
                          f"≥{settings.movement_min_time_separation_hours}h "
                          f"(have {n_obs} over {span:.1f}h)"}

    cur = window_velocity(views, now, window_hours=w)
    prior = window_velocity(views, now, window_hours=w, offset_windows=1)
    cur_v = cur[0] if cur else None
    prior_v = prior[0] if prior else None

    cohort = list(baseline_values)
    baseline_median = _median(cohort)
    have_baseline = baseline_median is not None and len(cohort) >= settings.movement_min_baseline_sample \
        and baseline_median > 0

    ratio = (cur_v / baseline_median) if (have_baseline and cur_v is not None) else None
    accel = (cur_v / prior_v) if (cur_v is not None and prior_v not in (None, 0)) else None
    comment_v = None
    if comments:
        cv = window_velocity(comments, now, window_hours=w)
        comment_v = cv[0] if cv else None

    calc["supporting_values"].update({
        "current_velocity_per_hour": round(cur_v, 3) if cur_v is not None else None,
        "prior_velocity_per_hour": round(prior_v, 3) if prior_v is not None else None,
        "baseline_median_velocity": round(baseline_median, 3) if baseline_median is not None else None,
        "velocity_ratio_vs_baseline": round(ratio, 3) if ratio is not None else None,
        "acceleration": round(accel, 3) if accel is not None else None,
        "comment_velocity_per_hour": round(comment_v, 3) if comment_v is not None else None})
    calc["comparison_cohort"] = f"owned videos aged {bucket_label}" if bucket_label else None
    calc["baseline_sample_size"] = len(cohort)

    # deterministic classification (order matters: breakout ⊃ rising).
    young = age is not None and age <= settings.movement_breakout_max_age_hours
    if (young and ratio is not None and ratio >= settings.movement_breakout_ratio
            and accel is not None and accel >= settings.movement_breakout_accel_ratio):
        state = BREAKOUT_CANDIDATE
    elif ratio is not None and ratio >= settings.movement_rising_ratio:
        state = RISING
    elif accel is not None and accel <= settings.movement_cooling_ratio and prior_v and prior_v > 0:
        state = COOLING
    elif ratio is None:
        # a velocity exists but there is no comparable-age cohort to judge it against → honest N/A
        return {**calc, "classification": INSUFFICIENT_HISTORY, "evidence_state": "NO_BASELINE",
                "reason": f"velocity known but only {len(cohort)} comparable-age videos "
                          f"(need ≥{settings.movement_min_baseline_sample}) for a baseline"}
    else:
        state = NORMAL
    return {**calc, "classification": state, "evidence_state": "SUFFICIENT"}


def monitored_artists(db: Session, *, limit: int = 500) -> list[str]:
    """Canonical artists that have any tracked YouTube content (the content-sensing cohort)."""
    from sqlalchemy import func, select
    return list(db.execute(
        select(func.distinct(YouTubeVideo.canonical_artist_id)).limit(limit)).scalars())


def market_movement(db: Session, *, now: datetime | None = None, limit: int = 200) -> dict[str, Any]:
    """Market-wide notable YouTube movement across monitored artists (bounded). No leaderboard, no score:
    every item carries the artist, the video, its classification and its evidence."""
    now = now or _now()
    artists = monitored_artists(db, limit=limit)
    breakout: list[dict[str, Any]] = []
    rising: list[dict[str, Any]] = []
    cooling: list[dict[str, Any]] = []
    cross_channel: list[dict[str, Any]] = []
    for a in artists:
        mv = artist_movement(db, a, now=now)
        for r in mv["breakout_candidates"]:
            breakout.append({"canonical_artist_id": a, **r})
        for r in mv["rising"]:
            rising.append({"canonical_artist_id": a, **r})
        for r in mv["cooling"]:
            cooling.append({"canonical_artist_id": a, **r})
        if mv["cross_channel_activity"]:
            cross_channel.append({"canonical_artist_id": a,
                                  "independent_active_channels": mv["independent_active_channels"],
                                  "moving_owned": mv["moving_owned"],
                                  "moving_ecosystem": mv["moving_ecosystem"]})

    def _by_velocity(items):
        return sorted(items, key=lambda r: r["supporting_values"].get("current_velocity_per_hour") or 0,
                      reverse=True)
    return {
        "calculated_at": now.isoformat(),
        "artists_considered": len(artists),
        "breakout_candidates": _by_velocity(breakout),
        "rising": _by_velocity(rising),
        "cooling": cooling,
        "cross_channel_activity": cross_channel,
        "disclaimer": "Observed abnormal movement on a bounded set of tracked videos — not a prediction "
                      "or a ranking of importance. Each item explains what moved and against what baseline.",
    }


def _load_video_series(db: Session, artist: str) -> tuple[dict[str, Series], dict[str, Series]]:
    return (reads.content_series(db, artist, YT_VIDEO_VIEWS),
            reads.content_series(db, artist, YT_VIDEO_COMMENTS))


def artist_movement(db: Session, artist: str, *, now: datetime | None = None,
                    relationship: str | None = None) -> dict[str, Any]:
    """Artist-level movement derived from constituent per-video evidence — never one fused number.

    Returns per-state counts, the breakout/rising/cooling videos (each with its evidence), the highest
    observed velocity, owned-vs-ecosystem moving counts, the number of independent active channels, and a
    cross-channel-activity flag. ``relationship`` optionally restricts to OWNED_CONTENT / ECOSYSTEM_CONTENT."""
    now = now or _now()
    videos = vids.videos_for_artist(db, artist, active_only=False, limit=1000)
    if relationship:
        videos = [v for v in videos if v.relationship_type == relationship]
    views_by, comments_by = _load_video_series(db, artist)
    owned = [v for v in videos if v.relationship_type == "OWNED_CONTENT"]
    baselines = build_baselines(db, artist, owned, now, views_by)

    results: list[dict[str, Any]] = []
    for v in videos:
        label = _age_bucket_label(_hours(_aware(v.published_at), now)) if v.published_at else None
        cohort = [vel for (vid, vel) in baselines.get(label, []) if vid != v.video_id]
        results.append(video_movement(
            v, views=views_by.get(v.video_id, []), comments=comments_by.get(v.video_id),
            baseline_values=cohort, bucket_label=label, now=now))

    counts: dict[str, int] = {s: 0 for s in (INSUFFICIENT_HISTORY, NORMAL, RISING, BREAKOUT_CANDIDATE, COOLING)}
    for r in results:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1
    moving = [r for r in results if r["classification"] in (RISING, BREAKOUT_CANDIDATE)]
    velocities = [r["supporting_values"].get("current_velocity_per_hour") for r in results]
    highest = max([v for v in velocities if v is not None], default=None)
    moving_channels = {r["channel_id"] for r in moving if r.get("channel_id")}
    cross_channel = len(moving_channels) >= settings.movement_cross_channel_min_channels

    return {
        "canonical_artist_id": artist,
        "calculated_at": now.isoformat(),
        "videos_considered": len(videos),
        "counts": counts,
        "moving_owned": sum(1 for r in moving if r["relationship_type"] == "OWNED_CONTENT"),
        "moving_ecosystem": sum(1 for r in moving if r["relationship_type"] == "ECOSYSTEM_CONTENT"),
        "highest_velocity_per_hour": round(highest, 3) if highest is not None else None,
        "independent_active_channels": len(moving_channels),
        "cross_channel_activity": cross_channel,
        "breakout_candidates": [r for r in results if r["classification"] == BREAKOUT_CANDIDATE],
        "rising": [r for r in results if r["classification"] == RISING],
        "cooling": [r for r in results if r["classification"] == COOLING],
        "disclaimer": "Movement is observed abnormal behaviour on a bounded set of tracked videos — not a "
                      "prediction, not a virality score. Each item carries its own evidence.",
    }
