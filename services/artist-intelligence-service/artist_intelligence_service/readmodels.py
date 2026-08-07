"""Deterministic demand read models (Phase 5A) — descriptive, explainable, no scores.

Everything here is a plain arithmetic transform of stored observations with transparent status flags.
Nothing is called popularity / market value / ticket demand / booking potential, nothing is
extrapolated, and no causal claim is made. Sparse history returns ``INSUFFICIENT_HISTORY`` honestly.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from artist_intelligence_service.config import settings

INSUFFICIENT = "INSUFFICIENT_HISTORY"
OK = "OK"

Series = list[tuple[datetime, float]]


# ---- window deltas + velocity -----------------------------------------------------------------
def window_delta(s: Series, now: datetime, days: int, *, min_obs: int | None = None) -> dict[str, Any]:
    """Change of a metric over the last ``days`` days + per-day velocity, or INSUFFICIENT_HISTORY.

    Baseline = the latest observation at/before (now - days); requires real history covering the
    window (no extrapolation). Velocity is delta / actual span in days."""
    min_obs = settings.demand_min_observations_for_delta if min_obs is None else min_obs
    if len(s) < min_obs:
        return {"status": INSUFFICIENT, "reason": "too_few_observations", "observations": len(s)}
    latest_dt, latest_v = s[-1]
    cutoff = now - timedelta(days=days)
    baselines = [(dt, v) for dt, v in s if dt <= cutoff]
    if not baselines:
        return {"status": INSUFFICIENT, "reason": "history_shorter_than_window",
                "observations": len(s), "window_days": days}
    base_dt, base_v = baselines[-1]
    span_days = max((latest_dt - base_dt).total_seconds() / 86400.0, 1e-9)
    delta = latest_v - base_v
    return {"status": OK, "window_days": days, "delta": delta,
            "velocity_per_day": round(delta / span_days, 4), "span_days": round(span_days, 3),
            "from": {"observed_at": base_dt.isoformat(), "value": base_v},
            "to": {"observed_at": latest_dt.isoformat(), "value": latest_v}}


def latest_value(s: Series) -> dict[str, Any] | None:
    if not s:
        return None
    dt, v = s[-1]
    return {"observed_at": dt.isoformat(), "value": v}


# ---- momentum components (returned INDEPENDENTLY — never combined into a score) ----------------
def content_velocity(per_video: dict[str, Series], now: datetime) -> dict[str, Any]:
    """Mean per-video daily view growth across tracked videos with >=2 snapshots."""
    per: list[float] = []
    for s in per_video.values():
        if len(s) >= 2:
            span = max((s[-1][0] - s[0][0]).total_seconds() / 86400.0, 1e-9)
            per.append((s[-1][1] - s[0][1]) / span)
    if not per:
        return {"status": INSUFFICIENT, "reason": "no_video_has_two_snapshots",
                "videos_tracked": len(per_video)}
    return {"status": OK, "videos_measured": len(per),
            "mean_view_velocity_per_day": round(sum(per) / len(per), 4)}


def upload_activity(published_dates: list[datetime], now: datetime) -> dict[str, Any]:
    """recent_upload_count_30d + a simple cadence (uploads/week over the observed window)."""
    if not published_dates:
        return {"status": INSUFFICIENT, "reason": "no_upload_timestamps"}
    recent = [d for d in published_dates if d >= now - timedelta(days=30)]
    span_days = max((now - min(published_dates)).total_seconds() / 86400.0, 1e-9)
    return {"status": OK, "recent_upload_count_30d": len(recent),
            "uploads_per_week": round(len(published_dates) / (span_days / 7.0), 3),
            "window_days_observed": round(span_days, 1)}


def engagement_ratio(latest_views: float | None, latest_likes: float | None,
                     latest_comments: float | None) -> dict[str, Any]:
    """(likes + comments) / views on the most recent tracked video — a current-state ratio, not a score."""
    if not latest_views:
        return {"status": INSUFFICIENT, "reason": "no_view_count"}
    engagements = (latest_likes or 0) + (latest_comments or 0)
    return {"status": OK, "engagement_ratio": round(engagements / latest_views, 6),
            "views": latest_views, "likes": latest_likes, "comments": latest_comments}


# ---- freshness --------------------------------------------------------------------------------
def freshness(last_observed: datetime | None, now: datetime) -> dict[str, Any]:
    if last_observed is None:
        return {"status": "NO_DATA", "stale": True}
    age_h = (now - last_observed).total_seconds() / 3600.0
    return {"last_observed_at": last_observed.isoformat(), "age_hours": round(age_h, 2),
            "stale": age_h > settings.demand_freshness_stale_hours,
            "stale_threshold_hours": settings.demand_freshness_stale_hours}


# ---- geographic affinity ----------------------------------------------------------------------
def geo_affinity_label(interest: float | None, supply_count: int) -> str:
    """Transparent, configurable labels relative to the analysed cohort. Prefer the raw measures."""
    hi, lo = settings.geo_affinity_high_interest_threshold, settings.geo_affinity_low_interest_threshold
    if interest is None:
        return "LOW_EVIDENCE"
    demand_high = interest >= hi
    demand_low = interest <= lo
    supply_high = supply_count >= 1
    if demand_high and not supply_high:
        return "HIGHER_OBSERVED_DEMAND / LOWER_OBSERVED_SUPPLY"
    if demand_high and supply_high:
        return "HIGHER_OBSERVED_DEMAND / HIGHER_OBSERVED_SUPPLY"
    if demand_low and supply_high:
        return "LOWER_OBSERVED_DEMAND / HIGHER_OBSERVED_SUPPLY"
    return "LOW_EVIDENCE"


# ---- event-response timeline (co-movement only; explicitly NOT causal) -------------------------
_OFFSETS = [-60, -30, -14, -7, 0, 7]


def _nearest(s: Series, target: datetime) -> float | None:
    if not s:
        return None
    return min(s, key=lambda p: abs((p[0] - target).total_seconds()))[1]


def event_response_timeline(*, starts_at: datetime, trends_country: Series,
                            channel_views: Series, ledger_transitions: list[dict[str, Any]],
                            now: datetime) -> dict[str, Any]:
    """Deterministic T-relative view (T-60..T+7): Trends interest, YouTube view velocity, and
    ticket/price/creative transitions aligned to each offset. Returns co-movement ONLY."""
    rows = []
    for off in _OFFSETS:
        target = starts_at + timedelta(days=off)
        interest = _nearest(trends_country, target) if trends_country else None
        # view velocity around the offset: slope of the two channel snapshots bracketing target
        vv = None
        if len(channel_views) >= 2:
            before = [p for p in channel_views if p[0] <= target]
            after = [p for p in channel_views if p[0] >= target]
            if before and after and after[0][0] > before[-1][0]:
                span = max((after[0][0] - before[-1][0]).total_seconds() / 86400.0, 1e-9)
                vv = round((after[0][1] - before[-1][1]) / span, 2)
        transitions = [t for t in ledger_transitions
                       if _within(t.get("observed_at"), target, days=3)]
        rows.append({"offset_days": off, "date": target.isoformat(),
                     "google_search_interest": interest, "youtube_view_velocity_per_day": vv,
                     "ticket_price_creative_transitions": transitions,
                     "has_data": any(x is not None for x in (interest, vv)) or bool(transitions)})
    covered = sum(1 for r in rows if r["has_data"])
    return {"event_starts_at": starts_at.isoformat(), "timeline": rows,
            "offsets_with_data": covered, "offsets_total": len(_OFFSETS),
            "status": OK if covered else INSUFFICIENT,
            "interpretation": "temporal co-movement only; no causal inference is claimed"}


def _within(observed_at: Any, target: datetime, *, days: int) -> bool:
    if not observed_at:
        return False
    try:
        dt = datetime.fromisoformat(str(observed_at))
    except ValueError:
        return False
    if dt.tzinfo is None:
        from datetime import UTC
        dt = dt.replace(tzinfo=UTC)
    return abs((dt - target).total_seconds()) <= days * 86400
