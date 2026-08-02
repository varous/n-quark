"""Deterministic commercial-state normalization, hashing and transition detection.

Phase 1 — Minimum Viable Shadow Ledger. Pure functions, no I/O, no LLM: given a raw public
commercial state and the previous stored state, produce a canonical normalized state, a stable
hash, and an explicit list of transitions. Storage and HTTP live elsewhere (shadow_store.py,
routes/shadow_ledger.py) so this detector can be reused unchanged by a future crawl-service.

Design rules honoured here:
- volatile fields (capture time) are NOT part of the hash — only meaningful commercial state is;
- null is distinct from zero;
- numeric equality is normalized (400 == 400.0; money to 2dp; ratio to 3dp);
- no monotonicity assumption — a *decrease* in tickets_sold is a legitimate value change, never
  inferred as refunds/fraud;
- disappearance requires *authoritative* absence and a configurable consecutive threshold — a single
  failed capture never emits EVENT_DISAPPEARED.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

DETECTOR_VERSION = "shadow-detector-1"

# --- epistemic status (mirrors ADR-0003); Boshow public ticket state is observed_public_state ----
OBSERVED_PUBLIC_STATE = "observed_public_state"
REPORTED_OUTCOME = "reported_outcome"
VERIFIED = "verified"
MODEL_ESTIMATE = "model_estimate"
UNKNOWN = "unknown"
EPISTEMIC_STATUSES = frozenset(
    {OBSERVED_PUBLIC_STATE, REPORTED_OUTCOME, VERIFIED, MODEL_ESTIMATE, UNKNOWN}
)

# --- transition vocabulary (Phase 1 only — do not add types without data to back them) -----------
EVENT_FIRST_SEEN = "EVENT_FIRST_SEEN"
PUBLIC_PRICE_CHANGED = "PUBLIC_PRICE_CHANGED"
PUBLIC_CAPACITY_CHANGED = "PUBLIC_CAPACITY_CHANGED"
PUBLIC_TICKETS_SOLD_CHANGED = "PUBLIC_TICKETS_SOLD_CHANGED"
PUBLIC_FILL_RATIO_CHANGED = "PUBLIC_FILL_RATIO_CHANGED"
PUBLIC_AVAILABILITY_CHANGED = "PUBLIC_AVAILABILITY_CHANGED"
EVENT_DATE_CHANGED = "EVENT_DATE_CHANGED"
VENUE_CHANGED = "VENUE_CHANGED"
EVENT_STATUS_CHANGED = "EVENT_STATUS_CHANGED"
EVENT_DISAPPEARED = "EVENT_DISAPPEARED"
EVENT_REAPPEARED = "EVENT_REAPPEARED"

# --- absence reasons: only *authoritative* ones count toward disappearance (ADR-0004) ------------
# Non-authoritative = an infra failure; the event may well still exist, so it never disappears it.
NON_AUTHORITATIVE_ABSENCE = frozenset({"capture_failure", "source_unavailable", "parser_failure"})
AUTHORITATIVE_ABSENCE = frozenset({"record_absent", "not_found", "explicitly_removed"})


def _norm_money(v: Any) -> float | None:
    return None if v is None else round(float(v), 2)


def _norm_ratio(v: Any) -> float | None:
    return None if v is None else round(float(v), 3)


def _norm_int(v: Any) -> int | None:
    return None if v is None else int(v)


def _norm_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _norm_dt(v: Any) -> str | None:
    """Normalize a date/datetime to a canonical ISO string; pass through if unparseable."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    s = str(v).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).isoformat()  # fromisoformat handles trailing 'Z' (py3.11+)
    except ValueError:
        return s


@dataclass(frozen=True)
class FieldSpec:
    name: str          # normalized field name (the key in normalized_state)
    source_field: str  # the raw field the caller supplies
    normalizer: Any    # callable value -> canonical value
    transition_type: str


# The supported commercial-state schema. Order is fixed for deterministic hashing.
FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("price_min", "price_min", _norm_money, PUBLIC_PRICE_CHANGED),
    FieldSpec("currency", "currency", _norm_str, PUBLIC_PRICE_CHANGED),
    FieldSpec("capacity", "capacity", _norm_int, PUBLIC_CAPACITY_CHANGED),
    FieldSpec("tickets_sold", "tickets_sold", _norm_int, PUBLIC_TICKETS_SOLD_CHANGED),
    FieldSpec("fill_ratio", "fill_ratio", _norm_ratio, PUBLIC_FILL_RATIO_CHANGED),
    FieldSpec("availability", "availability", _norm_str, PUBLIC_AVAILABILITY_CHANGED),
    FieldSpec("starts_at", "starts_at", _norm_dt, EVENT_DATE_CHANGED),
    FieldSpec("venue", "venue", _norm_str, VENUE_CHANGED),
    FieldSpec("status", "status", _norm_str, EVENT_STATUS_CHANGED),
)
# currency is grouped with price: a currency change surfaces as PUBLIC_PRICE_CHANGED, and currency
# alone is never emitted as its own transition (see _field_transitions).
_CURRENCY_WITH_PRICE = True

_DEFAULT_CONFIDENCE = {
    EVENT_FIRST_SEEN: 0.95,
    EVENT_DISAPPEARED: 0.7,
    EVENT_REAPPEARED: 0.8,
}


@dataclass
class Transition:
    transition_type: str
    field_name: str | None = None
    previous_value: Any = None
    current_value: Any = None
    confidence: float = 0.9
    epistemic_status: str = OBSERVED_PUBLIC_STATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition_type": self.transition_type,
            "field_name": self.field_name,
            "previous_value": self.previous_value,
            "current_value": self.current_value,
            "confidence": self.confidence,
            "epistemic_status": self.epistemic_status,
        }


@dataclass
class DetectionResult:
    normalized_state: dict[str, Any]
    state_hash: str
    transitions: list[Transition] = field(default_factory=list)
    noop: bool = False  # True when the incoming state equals the latest stored state (idempotent)
    trace: dict[str, Any] = field(default_factory=dict)


def normalize_state(
    raw: dict[str, Any],
    *,
    present: bool = True,
    absence_reason: str | None = None,
    prev_consecutive_absent: int = 0,
) -> dict[str, Any]:
    """Build the canonical, volatile-free normalized state used for hashing and diffing."""
    state: dict[str, Any] = {"present": bool(present)}
    if present:
        for spec in FIELD_SPECS:
            state[spec.name] = spec.normalizer(raw.get(spec.source_field))
        state["absence_reason"] = None
        state["consecutive_absent"] = 0
    else:
        # Absent capture: commercial fields are not observed; carry the reason + a running count of
        # consecutive *authoritative* absences so the disappearance threshold can be evaluated.
        for spec in FIELD_SPECS:
            state[spec.name] = None
        state["absence_reason"] = absence_reason
        authoritative = absence_reason in AUTHORITATIVE_ABSENCE
        state["consecutive_absent"] = (prev_consecutive_absent + 1) if authoritative else 0
    return state


def state_hash(normalized: dict[str, Any]) -> str:
    """Deterministic hash over the normalized state (stable key order, canonical JSON)."""
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _field_transitions(prev: dict[str, Any], new: dict[str, Any]) -> list[Transition]:
    out: list[Transition] = []
    seen_price = False
    for spec in FIELD_SPECS:
        pv, cv = prev.get(spec.name), new.get(spec.name)
        if pv == cv:
            continue
        if spec.name in ("price_min", "currency") and _CURRENCY_WITH_PRICE:
            if seen_price:
                continue  # already emitted a single PUBLIC_PRICE_CHANGED for this state pair
            seen_price = True
            out.append(
                Transition(
                    PUBLIC_PRICE_CHANGED,
                    "price_min",
                    {"price_min": prev.get("price_min"), "currency": prev.get("currency")},
                    {"price_min": new.get("price_min"), "currency": new.get("currency")},
                )
            )
            continue
        out.append(Transition(spec.transition_type, spec.name, pv, cv))
    return out


def detect_transitions(
    prev: dict[str, Any] | None,
    new: dict[str, Any],
    *,
    disappearance_threshold: int = 2,
) -> list[Transition]:
    """Compare the previous normalized state to the new one and emit explicit transitions.

    Deterministic and explainable. Handles first-sight, per-field changes (incl. decreases),
    and conservative disappearance/reappearance.
    """
    def conf(t: Transition) -> Transition:
        t.confidence = _DEFAULT_CONFIDENCE.get(t.transition_type, 0.9)
        return t

    new_present = new.get("present", True)

    if prev is None:
        # Never seen before. A first *present* sighting is EVENT_FIRST_SEEN; a first *absence*
        # (e.g. discovered-then-immediately-gone) reports nothing.
        return [conf(Transition(EVENT_FIRST_SEEN))] if new_present else []

    prev_present = prev.get("present", True)

    if prev_present and new_present:
        return _field_transitions(prev, new)

    if not new_present:
        reason = new.get("absence_reason")
        if reason in NON_AUTHORITATIVE_ABSENCE:
            return []  # infra failure — record the state, but never disappear the event
        if reason == "explicitly_removed":
            # Authoritative removal: announce once, on the transition into absence.
            return [conf(Transition(EVENT_DISAPPEARED, current_value={"absence_reason": reason}))] if prev_present else []
        # record_absent / not_found: announce once when the consecutive count hits the threshold.
        if new.get("consecutive_absent", 0) == disappearance_threshold:
            return [conf(Transition(EVENT_DISAPPEARED, current_value={"absence_reason": reason}))]
        return []

    # not prev_present and new_present -> possible reappearance
    if prev.get("consecutive_absent", 0) >= disappearance_threshold or prev.get("absence_reason") == "explicitly_removed":
        return [conf(Transition(EVENT_REAPPEARED))]
    return []  # was only a tentative blip that never became a disappearance


def dedup_key(to_state_id: str, transition_type: str, field_name: str | None) -> str:
    return f"{to_state_id}:{transition_type}:{field_name or ''}"
