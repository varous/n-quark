"""Provider quota accounting (Phase 5A, reworked for granular buckets in 5A.3).

Two layers:
- ``QuotaMeter`` — an in-memory tally accumulated while a provider runs (DB-free, testable).
- ``record_meter`` — folds a drained meter into BOTH the legacy per-day aggregate
  (``provider_quota_day``, back-compat) AND the granular per-bucket rows
  (``provider_quota_bucket_day``, the authoritative accounting used for allocation + reserve).

The actual YouTube calls happen in signal-service, but each call type has a deterministic nominal cost,
so this service accounts nominal usage as it drives acquisition. 5A.3 stops collapsing everything into one
synthetic pool: usage is bucketed into SEARCH / GENERAL_READ / VIDEO_STATS_BATCH, budgets are configurable
fractions of the daily pool, and the quota day follows the provider's real reset timezone (not UTC).
API keys are never touched here — only nominal unit costs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from artist_intelligence_service.config import settings
from artist_intelligence_service.models import ProviderQuotaBucketDay, ProviderQuotaDay
from artist_intelligence_service.providers.base import PROVIDER_YOUTUBE

# YouTube Data API v3 nominal quota costs (well-known constants; not the API key, just prices).
YT_SEARCH_UNITS = 100
YT_READ_UNITS = 1

# Quota buckets (5A.3).
BUCKET_SEARCH = "SEARCH"
BUCKET_GENERAL_READ = "GENERAL_READ"
BUCKET_VIDEO_BATCH = "VIDEO_STATS_BATCH"


@dataclass
class QuotaCall:
    kind: str            # "search" | "read" | "video_batch"
    bucket: str
    units: int
    ok: bool = True
    quota_error: bool = False


@dataclass
class QuotaMeter:
    calls: list[QuotaCall] = field(default_factory=list)

    def record(self, kind: str, units: int, *, bucket: str, ok: bool = True,
               quota_error: bool = False) -> None:
        self.calls.append(QuotaCall(kind=kind, bucket=bucket, units=units, ok=ok,
                                    quota_error=quota_error))

    def read(self, *, units: int = YT_READ_UNITS, ok: bool = True) -> None:
        self.record("read", units, bucket=BUCKET_GENERAL_READ, ok=ok)

    def search(self, *, ok: bool = True, quota_error: bool = False) -> None:
        self.record("search", YT_SEARCH_UNITS, bucket=BUCKET_SEARCH, ok=ok, quota_error=quota_error)

    def video_batch(self, *, units: int = YT_READ_UNITS, ok: bool = True) -> None:
        """videos.list batch statistics — 1 unit for up to 50 ids (far cheaper than per-video reads)."""
        self.record("video_batch", units, bucket=BUCKET_VIDEO_BATCH, ok=ok)

    def drain(self) -> list[QuotaCall]:
        out = list(self.calls)
        self.calls.clear()
        return out


def _now() -> datetime:
    return datetime.now(UTC)


def quota_date_for(provider: str, *, on: date | None = None, now: datetime | None = None) -> date:
    """The quota day for a provider. YouTube resets at midnight in its configured reset tz (Pacific by
    default), NOT UTC — so the accounting day rolls over when Google's quota actually resets."""
    if on is not None:
        return on
    if provider == PROVIDER_YOUTUBE:
        return settings.youtube_quota_date(now)
    return (now or _now()).astimezone(UTC).date()


# ---- legacy aggregate (provider_quota_day) -----------------------------------------------------
def get_or_create_day(db: Session, provider: str, *, on: date | None = None) -> ProviderQuotaDay:
    day = quota_date_for(provider, on=on)
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
            ProviderQuotaDay.quota_date == quota_date_for(provider, on=on),
        )
    ).scalar_one_or_none()
    return row.search_requests if row else 0


# ---- granular buckets (provider_quota_bucket_day) ----------------------------------------------
def get_or_create_bucket(db: Session, provider: str, bucket: str, *,
                         on: date | None = None) -> ProviderQuotaBucketDay:
    day = quota_date_for(provider, on=on)
    row = db.execute(
        select(ProviderQuotaBucketDay).where(
            ProviderQuotaBucketDay.provider == provider,
            ProviderQuotaBucketDay.quota_date == day,
            ProviderQuotaBucketDay.bucket == bucket,
        )
    ).scalar_one_or_none()
    if row is None:
        now = _now()
        row = ProviderQuotaBucketDay(
            id=hashlib.sha256(f"{provider}|{day.isoformat()}|{bucket}".encode()).hexdigest()[:32],
            provider=provider, quota_date=day, bucket=bucket, created_at=now, updated_at=now)
        db.add(row)
        db.flush()
    return row


def record_meter(db: Session, provider: str, meter: QuotaMeter, *, on: date | None = None) -> None:
    """Fold a drained meter into today's aggregate row AND its per-bucket rows."""
    calls = meter.drain()
    if not calls:
        return
    agg = get_or_create_day(db, provider, on=on)
    for c in calls:
        agg.requests += 1
        if c.kind == "search":
            agg.search_requests += 1
            agg.search_quota_units += c.units
        else:
            agg.non_search_quota_units += c.units
        if c.ok:
            agg.successful_calls += 1
        else:
            agg.failed_calls += 1
        if c.quota_error:
            agg.quota_errors += 1
        bucket = get_or_create_bucket(db, provider, c.bucket, on=on)
        bucket.requests += 1
        bucket.units += c.units
        bucket.successful_calls += 1 if c.ok else 0
        bucket.failed_calls += 0 if c.ok else 1
        bucket.quota_errors += 1 if c.quota_error else 0
        bucket.updated_at = _now()
    agg.updated_at = _now()
    db.flush()


# ---- budget + reserve enforcement (5A.3) -------------------------------------------------------
def _bucket_fraction(bucket: str) -> float:
    return {
        BUCKET_SEARCH: settings.youtube_bucket_fraction_search,
        BUCKET_GENERAL_READ: settings.youtube_bucket_fraction_general_read,
        BUCKET_VIDEO_BATCH: settings.youtube_bucket_fraction_video_batch,
    }.get(bucket, 0.0)


def bucket_budget(bucket: str) -> int:
    return int(settings.youtube_daily_quota_units * _bucket_fraction(bucket))


def reserve_units() -> int:
    return int(settings.youtube_daily_quota_units * settings.youtube_quota_reserve_fraction)


def usable_units() -> int:
    """Daily units the scheduler may intentionally spend (target utilization ⇒ keeps the reserve)."""
    return int(settings.youtube_daily_quota_units * settings.youtube_quota_target_utilization)


def bucket_used(db: Session, provider: str, bucket: str, *, on: date | None = None) -> int:
    row = db.execute(
        select(ProviderQuotaBucketDay.units).where(
            ProviderQuotaBucketDay.provider == provider,
            ProviderQuotaBucketDay.quota_date == quota_date_for(provider, on=on),
            ProviderQuotaBucketDay.bucket == bucket)
    ).scalar_one_or_none()
    return int(row or 0)


def total_used(db: Session, provider: str, *, on: date | None = None) -> int:
    row = db.execute(
        select(func.coalesce(func.sum(ProviderQuotaBucketDay.units), 0)).where(
            ProviderQuotaBucketDay.provider == provider,
            ProviderQuotaBucketDay.quota_date == quota_date_for(provider, on=on))
    ).scalar_one()
    return int(row or 0)


def can_spend(db: Session, provider: str, bucket: str, units: int, *, on: date | None = None) -> bool:
    """True iff spending ``units`` in ``bucket`` keeps BOTH the bucket budget and the global reserve.

    Reserve enforcement is what makes 'target 95% utilization' safe: the scheduler stops/slows before
    the last 5% so an operator/critical call always has headroom."""
    if bucket_used(db, provider, bucket, on=on) + units > bucket_budget(bucket):
        return False
    if total_used(db, provider, on=on) + units > usable_units():
        return False
    return True


def bucket_snapshot(db: Session, provider: str, *, on: date | None = None) -> dict:
    """Read-only per-bucket accounting for diagnostics (used / budget / remaining + reserve)."""
    day = quota_date_for(provider, on=on)
    rows = {r.bucket: r for r in db.execute(
        select(ProviderQuotaBucketDay).where(
            ProviderQuotaBucketDay.provider == provider,
            ProviderQuotaBucketDay.quota_date == day)).scalars()}
    buckets = {}
    for b in (BUCKET_SEARCH, BUCKET_GENERAL_READ, BUCKET_VIDEO_BATCH):
        r = rows.get(b)
        used = r.units if r else 0
        buckets[b] = {
            "used": used, "budget": bucket_budget(b), "remaining": max(bucket_budget(b) - used, 0),
            "requests": r.requests if r else 0, "successful_calls": r.successful_calls if r else 0,
            "failed_calls": r.failed_calls if r else 0, "quota_errors": r.quota_errors if r else 0}
    used_total = total_used(db, provider, on=on)
    return {
        "provider": provider, "quota_date": day.isoformat(), "reset_tz": settings.youtube_quota_reset_tz,
        "daily_quota_units": settings.youtube_daily_quota_units,
        "target_utilization": settings.youtube_quota_target_utilization,
        "usable_units": usable_units(), "reserve_units": reserve_units(),
        "used_total": used_total, "remaining_usable": max(usable_units() - used_total, 0),
        "buckets": buckets,
    }
