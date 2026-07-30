"""Deterministic, explainable demand scoring — no AI, no randomness.

Every score is a transparent function of graph signals, reported alongside the components and
weights that produced it. Reproducible: the same graph state always yields the same number.
This is the layer that turns "demand and supply on a shared node" into an actual readout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Momentum direction -> a 0..1 score. Trends momentum is a direction, not a level (see the
# google_trends adapter), so we map the category rather than trusting a raw index.
_MOMENTUM_SCORE = {"rising": 1.0, "steady": 0.6, "falling": 0.2, "unknown": 0.5}

# Component weights for the artist demand score. Only components actually present are used, and
# the score is renormalized over the present weights, so a missing signal never silently reads 0.
_WEIGHTS = {"momentum": 0.4, "popularity": 0.3, "reach": 0.3}

_REACH_CAP = 5  # number of strong regions that saturates geographic reach


def momentum_score(momentum: str | None, *, breakout: bool = False) -> float:
    base = _MOMENTUM_SCORE.get((momentum or "").lower(), 0.5)
    return round(min(base + (0.1 if breakout else 0.0), 1.0), 3)


def popularity_score(subscriber_count: Any) -> float | None:
    """Log-scaled subscriber count -> 0..1. None when unknown (so it is omitted, not zeroed)."""
    try:
        subs = int(subscriber_count)
    except (TypeError, ValueError):
        return None
    if subs <= 0:
        return None
    return round(min(math.log10(subs) / 9.0, 1.0), 3)  # 1k~0.33, 1M~0.67, 100M~0.89


def reach_score(num_regions: int) -> float:
    return round(min(num_regions / _REACH_CAP, 1.0), 3) if num_regions else 0.0


def _combine(components: dict[str, float]) -> float:
    """Weighted mean over *present* components, renormalized to their weights, scaled 0..100."""
    denom = sum(_WEIGHTS[k] for k in components)
    if not denom:
        return 0.0
    return round(sum(_WEIGHTS[k] * v for k, v in components.items()) / denom * 100, 1)


@dataclass
class ArtistDemand:
    entity_id: str
    demand_score: float
    components: dict[str, float]
    weights: dict[str, float]
    strongest_markets: list[str]
    missing_signals: list[str]
    explanation: str


def artist_demand(entity_id: str, node_properties: dict[str, Any], strong_regions: list[str]) -> ArtistDemand:
    """Score an artist's live-event demand from its graph node + STRONG_IN regions."""
    components: dict[str, float] = {}

    momentum = node_properties.get("search_momentum")
    if momentum is not None:
        components["momentum"] = momentum_score(momentum)

    pop = popularity_score(node_properties.get("subscriber_count"))
    if pop is not None:
        components["popularity"] = pop

    if strong_regions:
        components["reach"] = reach_score(len(strong_regions))

    score = _combine(components)
    missing = [k for k in _WEIGHTS if k not in components]
    top = strong_regions[:3]
    parts = []
    if "momentum" in components:
        parts.append(f"search momentum '{momentum}'")
    if "reach" in components:
        parts.append(f"demand across {len(strong_regions)} regions (top: {', '.join(top)})")
    if "popularity" in components:
        parts.append(f"digital popularity {node_properties.get('subscriber_count'):,} subscribers")
    explanation = (
        f"Demand {score}/100 from {', '.join(parts)}." if parts
        else "Insufficient signals to score demand."
    )
    if missing:
        explanation += f" No signal for: {', '.join(missing)}."
    return ArtistDemand(entity_id, score, components, dict(_WEIGHTS), top, missing, explanation)


@dataclass
class RegionIntelligence:
    region_id: str
    demand_signals: int
    supply_signals: int
    demanding_artists: list[str]
    events: list[str]
    avg_fill_ratio: float | None
    avg_price: float | None
    demand_supply_ratio: float
    verdict: str
    explanation: str
    contributing_sources: list[str] = field(default_factory=list)


def region_intelligence(
    region_id: str,
    demanding_artists: list[str],
    events: list[dict[str, Any]],
) -> RegionIntelligence:
    """Combine demand (artists STRONG_IN a region) with supply (events IN_REGION) into a readout.

    ``events`` are event node dicts ({id, properties}); demand/supply live on the same region
    node because every pipeline resolves to shared canonical entities.
    """
    demand = len(demanding_artists)
    supply = len(events)
    fills = [p["fill_ratio"] for e in events if isinstance((p := e.get("properties", {})).get("fill_ratio"), (int, float))]
    prices = [p["price_min"] for e in events if isinstance((p := e.get("properties", {})).get("price_min"), (int, float))]
    avg_fill = round(sum(fills) / len(fills), 3) if fills else None
    avg_price = round(sum(prices) / len(prices), 1) if prices else None
    ratio = round(demand / max(supply, 1), 2)

    if demand and supply == 0:
        verdict = "undersupplied"  # demand exists, no events yet — the opportunity signal
    elif ratio >= 1.5:
        verdict = "demand-led"
    elif supply and demand == 0:
        verdict = "supply-only"
    else:
        verdict = "balanced"

    fill_note = f", avg fill {avg_fill:.0%}" if avg_fill is not None else ""
    explanation = (
        f"{demand} artist(s) show demand vs {supply} event(s) supplying it "
        f"(ratio {ratio}{fill_note}) — {verdict}."
    )
    return RegionIntelligence(
        region_id, demand, supply, demanding_artists, [e.get("id") for e in events],
        avg_fill, avg_price, ratio, verdict, explanation,
    )
