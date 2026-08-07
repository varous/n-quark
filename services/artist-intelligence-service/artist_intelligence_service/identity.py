"""Deterministic id + observation-key helpers (Phase 5A).

``observation_key`` is what makes ingestion idempotent: re-ingesting the same provider fact yields the
same key and is a no-op, while genuine new history (a new day's snapshot, a new export) yields a new key.

Two bucketing strategies, chosen by the caller:
- live snapshots (YouTube): bucket = the observation DATE, so re-running today's refresh is idempotent
  but tomorrow's snapshot is preserved as new history;
- imports (Trends): bucket = the export fingerprint, so re-importing the same file is idempotent
  regardless of when, and independently-normalized exports stay distinct (never silently merged).
"""

from __future__ import annotations

import hashlib
import re


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-") or "unknown"


def new_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def observation_key(*, canonical_artist_id: str, provider: str, metric: str, scope_type: str,
                    scope_id: str | None, dedup_extra: str, bucket: str) -> str:
    raw = "|".join([canonical_artist_id, provider, metric, scope_type, scope_id or "-",
                    dedup_extra or "-", bucket])
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def export_fingerprint(*, label: str, geo: str, time_range: str | None,
                       comparison_window: str | None, source_file: str | None) -> str:
    """Stable id for one Trends export: same file/window/range/label ⇒ same fingerprint ⇒ idempotent."""
    raw = "|".join([label or "-", geo or "-", time_range or "-",
                    comparison_window or "-", source_file or "-"])
    return "exp:" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def pending_identity_id(canonical_artist_id: str) -> str:
    """The per-artist 'pending' YouTube identity slot used while a resolution is AMBIGUOUS/UNRESOLVED."""
    return f"pending:{_slug(canonical_artist_id)}"
