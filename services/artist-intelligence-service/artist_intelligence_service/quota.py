"""Provider quota accounting (Phase 5A).

Two layers:
- ``QuotaMeter`` — an in-memory tally accumulated while a provider runs (DB-free, testable).
- ``record_meter`` — folds a drained meter into the persistent ``provider_quota_day`` row.

The actual YouTube calls happen in signal-service, but the *cost* is deterministic per call type
(search = 100 units, read = 1 unit), so this service accounts nominal usage as it drives acquisition —
search vs non-search units are tracked separately, making the discipline (search is for identity
discovery, not measurement) visible.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from artist_intelligence_service.models import ProviderQuotaDay

# YouTube Data API v3 nominal quota costs (well-known constants).
YT_SEARCH_UNITS = 100
YT_READ_UNITS = 1


@dataclass
class QuotaCall:
    kind: str            # "search" | "read"
    units: int
    ok: bool = True
    quota_error: bool = False


@dataclass
class QuotaMeter:
    calls: list[QuotaCall] = field(default_factory=list)

    def record(self, kind: str, units: int, *, ok: bool = True, quota_error: bool = False) -> None:
        self.calls.append(QuotaCall(kind=kind, units=units, ok=ok, quota_error=quota_error))

    def read(self, *, units: int = YT_READ_UNITS, ok: bool = True) -> None:
        self.record("read", units, ok=ok)

    def search(self, *, ok: bool = True, quota_error: bool = False) -> None:
        self.record("search", YT_SEARCH_UNITS, ok=ok, quota_error=quota_error)

    def drain(self) -> list[QuotaCall]:
        out = list(self.calls)
        self.calls.clear()
        return out


def _now() -> datetime:
    return datetime.now(UTC)


def get_or_create_day(db: Session, provider: str, *, on: date | None = None) -> ProviderQuotaDay:
    day = on or _now().date()
    row = db.execute(
        select(ProviderQuotaDay).where(
            ProviderQuotaDay.provider == provider, ProviderQuotaDay.quota_date == day
        )
    ).scalar_one_or_none()
    if row is None:
        now = _now()
        row = ProviderQuotaDay(
            id=hashlib.sha256(f"{provider}|{day.isoformat()}".encode()).hexdigest()[:32],
            provider=provider, quota_date=day, created_at=now, updated_at=now,
        )
        db.add(row)
        db.flush()
    return row


def search_calls_today(db: Session, provider: str, *, on: date | None = None) -> int:
    row = db.execute(
        select(ProviderQuotaDay).where(
            ProviderQuotaDay.provider == provider,
            ProviderQuotaDay.quota_date == (on or _now().date()),
        )
    ).scalar_one_or_none()
    return row.search_requests if row else 0


def record_meter(db: Session, provider: str, meter: QuotaMeter, *, on: date | None = None) -> None:
    """Fold a provider's drained meter into today's persistent quota row."""
    calls = meter.drain()
    if not calls:
        return
    row = get_or_create_day(db, provider, on=on)
    for c in calls:
        row.requests += 1
        if c.kind == "search":
            row.search_requests += 1
            row.search_quota_units += c.units
        else:
            row.non_search_quota_units += c.units
        if c.ok:
            row.successful_calls += 1
        else:
            row.failed_calls += 1
        if c.quota_error:
            row.quota_errors += 1
    row.updated_at = _now()
    db.flush()
