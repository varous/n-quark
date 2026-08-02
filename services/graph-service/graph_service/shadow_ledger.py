"""Deterministic commercial-state normalization, hashing and transition detection.

Phase 1 + Phase 1.1. Pure functions, no I/O, no LLM. Phase 1.1 makes captures *completeness-aware*
so that incomplete / partial / failed captures can never fabricate a transition:

- a capture declares COMPLETE or PARTIAL;
- every field carries an observation status (OBSERVED_VALUE / OBSERVED_NULL / NOT_OBSERVED /
  EXTRACTION_FAILED / NOT_SUPPORTED);
- the *effective* state is the previous effective state with only the validly-observed fields
  overlaid — unobserved fields are carried forward, never nulled;
- a value->null transition is emitted only for an explicit OBSERVED_NULL on a field whose registry
  entry permits explicit null;
- decreases are preserved (no refund inferred); null is distinct from zero; numbers/timestamps are
  canonicalized.

Disappearance is handled at the store layer (capture_status + absence_count), not by field nulling.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

DETECTOR_VERSION = "shadow-detector-2"

# --- epistemic status (ADR-0003) -----------------------------------------------------------------
OBSERVED_PUBLIC_STATE = "observed_public_state"
REPORTED_OUTCOME = "reported_outcome"
VERIFIED = "verified"
MODEL_ESTIMATE = "model_estimate"
UNKNOWN = "unknown"
EPISTEMIC_STATUSES = frozenset({OBSERVED_PUBLIC_STATE, REPORTED_OUTCOME, VERIFIED, MODEL_ESTIMATE, UNKNOWN})

# --- snapshot completeness (Phase 1.1) -----------------------------------------------------------
COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
COMPLETENESS = frozenset({COMPLETE, PARTIAL})

# --- field-level observation status (Phase 1.1) --------------------------------------------------
OBSERVED_VALUE = "OBSERVED_VALUE"      # a concrete value was observed
OBSERVED_NULL = "OBSERVED_NULL"        # the source explicitly represented the field as empty/removed
NOT_OBSERVED = "NOT_OBSERVED"          # this capture did not evaluate/receive the field
EXTRACTION_FAILED = "EXTRACTION_FAILED"  # tried to extract but could not do so reliably
NOT_SUPPORTED = "NOT_SUPPORTED"        # the source/adapter does not expose this field
FIELD_STATUSES = frozenset({OBSERVED_VALUE, OBSERVED_NULL, NOT_OBSERVED, EXTRACTION_FAILED, NOT_SUPPORTED})
# Only these two participate in transition detection at all:
_OBSERVING = frozenset({OBSERVED_VALUE, OBSERVED_NULL})

# --- capture status (Phase 1.1 disappearance) ----------------------------------------------------
CAPTURE_SUCCESS_RECORD_PRESENT = "CAPTURE_SUCCESS_RECORD_PRESENT"
CAPTURE_SUCCESS_RECORD_ABSENT = "CAPTURE_SUCCESS_RECORD_ABSENT"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
CAPTURE_FAILED = "CAPTURE_FAILED"
PARSER_FAILED = "PARSER_FAILED"
NOT_CHECKED = "NOT_CHECKED"
EXPLICITLY_REMOVED = "EXPLICITLY_REMOVED"
CAPTURE_STATUSES = frozenset({
    CAPTURE_SUCCESS_RECORD_PRESENT, CAPTURE_SUCCESS_RECORD_ABSENT, SOURCE_UNAVAILABLE,
    CAPTURE_FAILED, PARSER_FAILED, NOT_CHECKED, EXPLICITLY_REMOVED,
})
# Only authoritative evidence of absence counts toward disappearance:
AUTHORITATIVE_ABSENCE = frozenset({CAPTURE_SUCCESS_RECORD_ABSENT, EXPLICITLY_REMOVED})
NON_AUTHORITATIVE = frozenset({SOURCE_UNAVAILABLE, CAPTURE_FAILED, PARSER_FAILED, NOT_CHECKED})

# --- transition vocabulary (Phase 1) -------------------------------------------------------------
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

# --- suppression reasons (Phase 1.1 observability) -----------------------------------------------
FIELD_NOT_OBSERVED = "FIELD_NOT_OBSERVED"
S_EXTRACTION_FAILED = "EXTRACTION_FAILED"
S_NOT_SUPPORTED = "NOT_SUPPORTED"
OUT_OF_ORDER = "OUT_OF_ORDER"
NO_VALUE_CHANGE = "NO_VALUE_CHANGE"
DUPLICATE_STATE = "DUPLICATE_STATE"
EXPLICIT_NULL_NOT_ALLOWED = "EXPLICIT_NULL_NOT_ALLOWED"
DISAPPEARANCE_THRESHOLD_NOT_MET = "DISAPPEARANCE_THRESHOLD_NOT_MET"
CONFLICTING_TIMESTAMP = "CONFLICTING_TIMESTAMP"


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
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    s = str(v).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s).isoformat()  # handles trailing 'Z' (py3.11+)
    except ValueError:
        return s


@dataclass(frozen=True)
class FieldSpec:
    name: str                     # key in effective state + field_status
    source_field: str             # raw field the caller supplies
    normalizer: Any               # callable value -> canonical value
    transition_type: str
    explicit_null_allowed: bool = False  # can an OBSERVED_NULL emit a value->null transition?


# The authoritative field registry — the single source of truth shared by the normalizer, the
# effective-state hasher and the transition detector.
FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("price_min", "price_min", _norm_money, PUBLIC_PRICE_CHANGED),
    FieldSpec("currency", "currency", _norm_str, PUBLIC_PRICE_CHANGED),
    FieldSpec("capacity", "capacity", _norm_int, PUBLIC_CAPACITY_CHANGED),
    FieldSpec("tickets_sold", "tickets_sold", _norm_int, PUBLIC_TICKETS_SOLD_CHANGED),
    FieldSpec("fill_ratio", "fill_ratio", _norm_ratio, PUBLIC_FILL_RATIO_CHANGED),
    # availability / event_status: an explicit null can mean the source *removed* the field.
    FieldSpec("availability", "availability", _norm_str, PUBLIC_AVAILABILITY_CHANGED, explicit_null_allowed=True),
    FieldSpec("starts_at", "starts_at", _norm_dt, EVENT_DATE_CHANGED),
    FieldSpec("venue", "venue", _norm_str, VENUE_CHANGED),
    FieldSpec("status", "status", _norm_str, EVENT_STATUS_CHANGED, explicit_null_allowed=True),
)
_SPEC_BY_NAME = {s.name: s for s in FIELD_SPECS}
_PRICE_FIELDS = ("price_min", "currency")

_DEFAULT_CONFIDENCE = {EVENT_FIRST_SEEN: 0.95, EVENT_DISAPPEARED: 0.7, EVENT_REAPPEARED: 0.8}


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
class CaptureEvaluation:
    effective_state: dict[str, Any]         # the merged, complete effective commercial state
    transitions: list[Transition] = field(default_factory=list)
    suppressed: list[dict[str, Any]] = field(default_factory=list)  # [{field, reason}]
    first_seen: bool = False


def resolve_field_statuses(
    values: dict[str, Any],
    provided: dict[str, str] | None,
    completeness: str,
) -> dict[str, str]:
    """Determine each registry field's observation status.

    Explicit caller-provided statuses always win. Otherwise infer conservatively — critically, a
    ``None`` value is NEVER inferred as OBSERVED_NULL (a model default of None is not proof the
    source represented the field as empty); it becomes NOT_OBSERVED.
    """
    provided = provided or {}
    out: dict[str, str] = {}
    for spec in FIELD_SPECS:
        explicit = provided.get(spec.name)
        if explicit in FIELD_STATUSES:
            out[spec.name] = explicit
            continue
        raw = values.get(spec.source_field)
        # Inference: a concrete value -> OBSERVED_VALUE; anything else (missing or None) ->
        # NOT_OBSERVED, regardless of COMPLETE/PARTIAL. We never invent OBSERVED_NULL.
        out[spec.name] = OBSERVED_VALUE if raw is not None else NOT_OBSERVED
    return out


def _canonical(spec: FieldSpec, raw: Any) -> Any:
    return spec.normalizer(raw)


def effective_state_hash(effective: dict[str, Any]) -> str:
    """Deterministic hash over the effective commercial state (registry fields only)."""
    payload = {name: effective.get(name) for name in _SPEC_BY_NAME}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def capture_hash(values: dict[str, Any], statuses: dict[str, str]) -> str:
    """Deterministic hash of what THIS capture actually observed (values + statuses)."""
    observed = {
        spec.name: _canonical(spec, values.get(spec.source_field))
        for spec in FIELD_SPECS
        if statuses.get(spec.name) in _OBSERVING
    }
    payload = {"observed": observed, "statuses": {k: statuses[k] for k in sorted(statuses)}}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _confidence(t: Transition) -> Transition:
    t.confidence = _DEFAULT_CONFIDENCE.get(t.transition_type, 0.9)
    return t


def evaluate_present_capture(
    prev_effective: dict[str, Any] | None,
    values: dict[str, Any],
    statuses: dict[str, str],
) -> CaptureEvaluation:
    """Merge a present capture onto the previous effective state and emit safe transitions.

    Unobserved / failed / unsupported fields are carried forward (never nulled, never a transition).
    On a brand-new event (no previous effective state) emit EVENT_FIRST_SEEN only.
    """
    first_seen = prev_effective is None
    prev = prev_effective or {}
    effective: dict[str, Any] = {}
    transitions: list[Transition] = []
    suppressed: list[dict[str, Any]] = []

    # Resolve each field's effective value + record why a transition was or wasn't emitted.
    for spec in FIELD_SPECS:
        st = statuses.get(spec.name, NOT_OBSERVED)
        prev_v = prev.get(spec.name)
        if st == OBSERVED_VALUE:
            effective[spec.name] = _canonical(spec, values.get(spec.source_field))
        elif st == OBSERVED_NULL:
            if spec.explicit_null_allowed:
                effective[spec.name] = None
            else:
                effective[spec.name] = prev_v  # carry forward; explicit null not meaningful here
                suppressed.append({"field": spec.name, "reason": EXPLICIT_NULL_NOT_ALLOWED})
        else:  # NOT_OBSERVED / EXTRACTION_FAILED / NOT_SUPPORTED -> carry forward, never a transition
            effective[spec.name] = prev_v
            if st == EXTRACTION_FAILED:
                suppressed.append({"field": spec.name, "reason": S_EXTRACTION_FAILED})
            elif st == NOT_OBSERVED:
                suppressed.append({"field": spec.name, "reason": FIELD_NOT_OBSERVED})
            # NOT_SUPPORTED is expected/quiet — no suppression noise.

    if first_seen:
        return CaptureEvaluation(effective, [_confidence(Transition(EVENT_FIRST_SEEN))], suppressed, True)

    # Price + currency collapse into a single PUBLIC_PRICE_CHANGED.
    price_observing = any(statuses.get(f) in _OBSERVING for f in _PRICE_FIELDS)
    price_changed = any(effective.get(f) != prev.get(f) for f in _PRICE_FIELDS)
    if price_observing and price_changed:
        transitions.append(Transition(
            PUBLIC_PRICE_CHANGED, "price_min",
            {"price_min": prev.get("price_min"), "currency": prev.get("currency")},
            {"price_min": effective.get("price_min"), "currency": effective.get("currency")},
        ))
    elif price_observing and not price_changed:
        suppressed.append({"field": "price_min", "reason": NO_VALUE_CHANGE})

    for spec in FIELD_SPECS:
        if spec.name in _PRICE_FIELDS:
            continue
        st = statuses.get(spec.name, NOT_OBSERVED)
        if st not in _OBSERVING:
            continue
        if st == OBSERVED_NULL and not spec.explicit_null_allowed:
            continue  # already suppressed above
        prev_v, cur_v = prev.get(spec.name), effective.get(spec.name)
        if prev_v != cur_v:
            transitions.append(Transition(spec.transition_type, spec.name, prev_v, cur_v))
        else:
            suppressed.append({"field": spec.name, "reason": NO_VALUE_CHANGE})

    return CaptureEvaluation(effective, transitions, suppressed, False)


def dedup_key(to_state_id: str, transition_type: str, field_name: str | None) -> str:
    return f"{to_state_id}:{transition_type}:{field_name or ''}"


def absence_step(
    prev_absence_count: int,
    prev_disappeared: bool,
    capture_status: str,
    threshold: int,
) -> tuple[int, bool, list[Transition], list[dict[str, Any]]]:
    """Evolve disappearance bookkeeping for an authoritative-absence capture.

    Returns (new_absence_count, now_disappeared, transitions, suppressed). Only authoritative
    absence reaches here; non-authoritative failures are filtered by the caller and never count.
    """
    if capture_status == EXPLICITLY_REMOVED:
        new_count = max(prev_absence_count, threshold)
        if prev_disappeared:
            return new_count, True, [], [{"field": None, "reason": DISAPPEARANCE_THRESHOLD_NOT_MET}]
        return new_count, True, [_confidence(Transition(
            EVENT_DISAPPEARED, current_value={"capture_status": capture_status}))], []
    # CAPTURE_SUCCESS_RECORD_ABSENT
    new_count = prev_absence_count + 1
    if new_count == threshold and not prev_disappeared:
        return new_count, True, [_confidence(Transition(
            EVENT_DISAPPEARED, current_value={"capture_status": capture_status}))], []
    if new_count >= threshold:
        return new_count, True, [], []  # already disappeared -> no duplicate
    return new_count, False, [], [{"field": None, "reason": DISAPPEARANCE_THRESHOLD_NOT_MET}]
