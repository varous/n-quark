"""Deterministic creative-transition detector (Phase 4B).

Pure logic over a per-(canonical_event_id, source, asset_role) current state and one new observation.
Emits source-specific media transitions; it does not touch the graph Shadow Ledger vocabulary.

Rules:
- identical content at a new URL is MEDIA_URL_CHANGED_SAME_CONTENT, never MEDIA_CONTENT_CHANGED;
- a failed fetch is not a disappearance (last valid state is preserved, no transition);
- disappearance requires an authoritative successful source capture with the asset reference absent;
- out-of-order observations are retained but never rewrite current state;
- transitions are idempotent (re-observing the same identity emits nothing).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from media_service.identity import identity_key

MEDIA_FIRST_SEEN = "MEDIA_FIRST_SEEN"
MEDIA_CONTENT_CHANGED = "MEDIA_CONTENT_CHANGED"
MEDIA_URL_CHANGED_SAME_CONTENT = "MEDIA_URL_CHANGED_SAME_CONTENT"
MEDIA_ROLE_CHANGED = "MEDIA_ROLE_CHANGED"
MEDIA_DISAPPEARED = "MEDIA_DISAPPEARED"
MEDIA_REAPPEARED = "MEDIA_REAPPEARED"


@dataclass(frozen=True)
class StateView:
    present: bool
    media_asset_id: str | None
    normalized_url: str | None
    first_seen_at: datetime
    last_observed_at: datetime
    last_changed_at: datetime
    version: int


@dataclass(frozen=True)
class ObsFacts:
    kind: str                      # "PRESENT" | "ABSENT"
    media_asset_id: str | None     # content id when fetched/hashed; None for URL-only or failed fetch
    normalized_url: str | None
    observed_at: datetime
    authoritative_absence: bool = False   # True only for a successful source capture missing the ref


@dataclass(frozen=True)
class Decision:
    transitions: list[str]
    new_state: StateView | None    # None => do not update current state
    out_of_order: bool
    changed: bool                  # whether current state changed


def _noop(prev: StateView | None) -> Decision:
    return Decision(transitions=[], new_state=None, out_of_order=False, changed=False)


def detect(prev: StateView | None, obs: ObsFacts) -> Decision:
    # out-of-order: retained as an observation, but never rewrites current state
    if prev is not None and obs.observed_at < prev.last_observed_at:
        return Decision(transitions=[], new_state=None, out_of_order=True, changed=False)

    if obs.kind == "ABSENT":
        if not obs.authoritative_absence:
            return _noop(prev)  # a failed fetch is NOT a disappearance
        if prev is None or not prev.present:
            return _noop(prev)  # already absent / never seen
        new = replace(prev, present=False, last_observed_at=obs.observed_at,
                      last_changed_at=obs.observed_at)
        return Decision([MEDIA_DISAPPEARED], new, out_of_order=False, changed=True)

    # obs.kind == "PRESENT"
    obs_key = identity_key(obs.media_asset_id, obs.normalized_url or "")

    if prev is None:
        new = StateView(present=True, media_asset_id=obs.media_asset_id,
                        normalized_url=obs.normalized_url, first_seen_at=obs.observed_at,
                        last_observed_at=obs.observed_at, last_changed_at=obs.observed_at, version=1)
        return Decision([MEDIA_FIRST_SEEN], new, out_of_order=False, changed=True)

    prev_key = identity_key(prev.media_asset_id, prev.normalized_url or "")

    if not prev.present:
        # reappearance (possibly with new content)
        transitions = [MEDIA_REAPPEARED]
        content_changed = bool(obs.media_asset_id and prev.media_asset_id
                               and obs.media_asset_id != prev.media_asset_id)
        version = prev.version + 1 if content_changed else prev.version
        if content_changed:
            transitions.append(MEDIA_CONTENT_CHANGED)
        new = replace(prev, present=True, media_asset_id=obs.media_asset_id or prev.media_asset_id,
                      normalized_url=obs.normalized_url or prev.normalized_url,
                      last_observed_at=obs.observed_at, last_changed_at=obs.observed_at, version=version)
        return Decision(transitions, new, out_of_order=False, changed=True)

    # prev is present
    if obs_key == prev_key:
        # same content id but a different URL → URL-only change (not a creative change)
        if obs.media_asset_id and obs.normalized_url and obs.normalized_url != prev.normalized_url:
            new = replace(prev, normalized_url=obs.normalized_url, last_observed_at=obs.observed_at)
            return Decision([MEDIA_URL_CHANGED_SAME_CONTENT], new, out_of_order=False, changed=True)
        # truly identical observation → idempotent (only freshen last_observed_at)
        new = replace(prev, last_observed_at=obs.observed_at)
        return Decision([], new, out_of_order=False, changed=False)

    # different identity while present → content changed
    new = replace(prev, media_asset_id=obs.media_asset_id, normalized_url=obs.normalized_url,
                  last_observed_at=obs.observed_at, last_changed_at=obs.observed_at,
                  version=prev.version + 1)
    return Decision([MEDIA_CONTENT_CHANGED], new, out_of_order=False, changed=True)
