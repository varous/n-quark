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

# YouTube Data API v3 nominal quota costs (current model; not the API key, just prices).
# Post-June-2026 semantics: search.list is metered in an INDEPENDENT "Search Queries" quota at 1 unit/call
# (default 100 calls/day) — it is NOT 100 units against the general 10,000-unit pool. General endpoints
# (channels.list, playlistItems.list, videos.list) are 1 unit each against the general pool.
YT_SEARCH_UNITS = 1
YT_READ_UNITS = 1

# The SEARCH bucket is INDEPENDENT of the general pool: its budget is a call/day count, and its spend is
# never summed into the general usage total. Only these buckets draw the shared general pool.
GENERAL_POOL_BUCKETS = ("GENERAL_READ", "VIDEO_STATS_BATCH")

# Quota buckets (5A.3).
BUCKET_SEARCH = "SEARCH"
BUCKET_GENERAL_READ = "GENERAL_READ"
BUCKET_VIDEO_BATCH = "VIDEO_STATS_BATCH"


# Search purposes (5A.3.1) — sub-accounting inside the SEARCH bucket for dynamic allocation.
SEARCH_UNRESOLVED = "unresolved"
SEARCH_DISCOVERY = "discovery"
SEARCH_AMBIGUITY = "ambiguity"
SEARCH_PURPOSES = (SEARCH_UNRESOLVED, SEARCH_DISCOVERY, SEARCH_AMBIGUITY)


@dataclass
class QuotaCall:
    kind: str            # "search" | "read" | "video_batch"
    bucket: str
    units: int
    ok: bool = True
    quota_error: bool = False
    purpose: str | None = None   # for search calls: unresolved | discovery | ambiguity


@dataclass
class QuotaMeter:
    calls: list[QuotaCall] = field(default_factory=list)

    def record(self, kind: str, units: int, *, bucket: str, ok: bool = True,
               quota_error: bool = False) -> None:
        self.calls.append(QuotaCall(kind=kind, bucket=bucket, units=units, ok=ok,
                                    quota_error=quota_error))

    def read(self, *, units: int = YT_READ_UNITS, ok: bool = True) -> None:
        self.record("read", units, bucket=BUCKET_GENERAL_READ, ok=ok)

    def search(self, *, ok: bool = True, quota_error: bool = False,
               purpose: str | None = None) -> None:
        self.calls.append(QuotaCall("search", BUCKET_SEARCH, YT_SEARCH_UNITS, ok=ok,
                                    quota_error=quota_error, purpose=purpose))

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
        # 5A.3.1: sub-account search spend by purpose (SEARCH:<purpose>) for dynamic allocation.
        if c.kind == "search" and c.purpose:
            sub = get_or_create_bucket(db, provider, f"{BUCKET_SEARCH}:{c.purpose}", on=on)
            sub.requests += 1
            sub.units += c.units
            sub.successful_calls += 1 if c.ok else 0
            sub.failed_calls += 0 if c.ok else 1
            sub.updated_at = _now()
    agg.updated_at = _now()
    db.flush()


# ---- budget + reserve enforcement (5A.3) -------------------------------------------------------
def _bucket_fraction(bucket: str) -> float:
    # Only the general-pool buckets are fractions of the 10,000-unit pool; SEARCH is independent.
    return {
        BUCKET_GENERAL_READ: settings.youtube_bucket_fraction_general_read,
        BUCKET_VIDEO_BATCH: settings.youtube_bucket_fraction_video_batch,
    }.get(bucket, 0.0)


def bucket_budget(bucket: str) -> int:
    """Per-bucket daily budget. SEARCH is the INDEPENDENT Search-Queries quota (a call/day count);
    GENERAL_READ / VIDEO_STATS_BATCH are fractions of the shared general 10,000-unit pool."""
    if bucket == BUCKET_SEARCH:
        return int(settings.youtube_search_daily_calls)
    return int(settings.youtube_daily_quota_units * _bucket_fraction(bucket))


def reserve_units() -> int:
    return int(settings.youtube_daily_quota_units * settings.youtube_quota_reserve_fraction)


def usable_units() -> int:
    """Daily units the scheduler may intentionally spend (target utilization ⇒ keeps the reserve)."""
    return int(settings.youtube_daily_quota_units * settings.youtube_quota_target_utilization)


def near_provider_reset(*, within_hours: float = 2.0, now: datetime | None = None) -> bool:
    """True when the provider quota day is within ``within_hours`` of resetting (in its reset tz), so
    otherwise-idle budget can be usefully saturated before it is lost. Never touches the reserve."""
    now = now or _now()
    try:
        from zoneinfo import ZoneInfo
        local = now.astimezone(ZoneInfo(settings.youtube_quota_reset_tz))
    except Exception:  # noqa: BLE001
        local = now.astimezone(UTC)
    seconds_into_day = local.hour * 3600 + local.minute * 60 + local.second
    return (86400 - seconds_into_day) <= within_hours * 3600


def bucket_used(db: Session, provider: str, bucket: str, *, on: date | None = None) -> int:
    row = db.execute(
        select(ProviderQuotaBucketDay.units).where(
            ProviderQuotaBucketDay.provider == provider,
            ProviderQuotaBucketDay.quota_date == quota_date_for(provider, on=on),
            ProviderQuotaBucketDay.bucket == bucket)
    ).scalar_one_or_none()
    return int(row or 0)


def bucket_requests(db: Session, provider: str, bucket: str, *, on: date | None = None) -> int:
    """Request (call) COUNT in a bucket today — the accounting unit for the independent Search-Queries
    quota (100 calls/day). Using calls, not units, makes SEARCH gating robust to any historical
    per-call unit-cost change: a day that accrued stale 100-unit search rows still gates on real calls."""
    row = db.execute(
        select(ProviderQuotaBucketDay.requests).where(
            ProviderQuotaBucketDay.provider == provider,
            ProviderQuotaBucketDay.quota_date == quota_date_for(provider, on=on),
            ProviderQuotaBucketDay.bucket == bucket)
    ).scalar_one_or_none()
    return int(row or 0)


def general_used(db: Session, provider: str, *, on: date | None = None) -> int:
    """Units drawn from the shared GENERAL pool = GENERAL_READ + VIDEO_STATS_BATCH ONLY.

    Deliberately excludes the independent SEARCH bucket AND every ``SEARCH:<purpose>`` sub-bucket (which
    are an allocation overlay, not additional spend) — so one provider request is never double-counted and
    an independent quota is never summed into the general total (5B.2.7 §6)."""
    row = db.execute(
        select(func.coalesce(func.sum(ProviderQuotaBucketDay.units), 0)).where(
            ProviderQuotaBucketDay.provider == provider,
            ProviderQuotaBucketDay.quota_date == quota_date_for(provider, on=on),
            ProviderQuotaBucketDay.bucket.in_(GENERAL_POOL_BUCKETS))
    ).scalar_one()
    return int(row or 0)


# Back-compat alias: "total used" now means the honest general-pool total (never the mixed sum).
total_used = general_used


def search_calls_used(db: Session, provider: str, *, on: date | None = None) -> int:
    """Search-Queries CALLS spent today (the independent 100/day quota; accounted by call count, not
    units — robust to the pre-5B.2.7 100-unit rows that may linger on the current quota day)."""
    return bucket_requests(db, provider, BUCKET_SEARCH, on=on)


def can_spend(db: Session, provider: str, bucket: str, units: int, *, on: date | None = None) -> bool:
    """True iff spending ``units`` in ``bucket`` is within budget.

    SEARCH is checked ONLY against its own independent Search-Queries quota, and by CALL COUNT (never the
    general pool, never stale units). A general-pool bucket is checked against BOTH its own budget and the
    shared general reserve — the reserve keeps 'target 95% utilization' safe (critical calls keep headroom)."""
    if bucket == BUCKET_SEARCH:
        # 1 search.list call = 1 unit; gate on call count so lingering 100-unit rows can't poison the day
        return search_calls_used(db, provider, on=on) + units <= settings.youtube_search_daily_calls
    if bucket_used(db, provider, bucket, on=on) + units > bucket_budget(bucket):
        return False
    if general_used(db, provider, on=on) + units > usable_units():
        return False
    return True


def _search_alloc_fraction(purpose: str) -> float:
    return {
        SEARCH_UNRESOLVED: settings.youtube_search_alloc_unresolved,
        SEARCH_DISCOVERY: settings.youtube_search_alloc_discovery,
        SEARCH_AMBIGUITY: settings.youtube_search_alloc_ambiguity,
    }.get(purpose, 0.0)


def search_purpose_budget(purpose: str) -> int:
    """A purpose's configured slice of the SEARCH bucket budget."""
    return int(bucket_budget(BUCKET_SEARCH) * _search_alloc_fraction(purpose))


def search_purpose_used(db: Session, provider: str, purpose: str, *, on: date | None = None) -> int:
    # calls, not units — consistent with the call-based Search-Queries quota (5B.2.8)
    return bucket_requests(db, provider, f"{BUCKET_SEARCH}:{purpose}", on=on)


def search_reserve_calls() -> int:
    """Calls held back from the ordinary backlog so high-priority work (new Watchlist / operator /
    evidence-triggered) always has search headroom (5B.2.8 §4). Configured, never hard-coded."""
    return int(settings.youtube_search_daily_calls * settings.youtube_search_alloc_reserve)


def can_spend_search(db: Session, provider: str, purpose: str, units: int, *,
                     others_have_backlog: bool = True, near_reset: bool = False,
                     high_priority: bool = False, on: date | None = None) -> bool:
    """Dynamic per-purpose SEARCH allocation (5A.3.1 + 5B.2.8 §4).

    Always requires the SEARCH call cap (``can_spend``). Ordinary backlog additionally keeps a configured
    search RESERVE so a later high-priority target (new Watchlist / operator / new evidence) is never
    starved; a ``high_priority`` job may use that reserve. Within the cap, a purpose may always use its own
    configured slice, and may BORROW beyond it only when other purposes are idle or the day is near reset."""
    if not settings.youtube_search_allocation_enforced:
        return can_spend(db, provider, BUCKET_SEARCH, units, on=on)
    if not can_spend(db, provider, BUCKET_SEARCH, units, on=on):
        return False
    if not high_priority:
        # keep the reserve for high-priority intake (unless the day is near reset — then use idle budget)
        reserve = search_reserve_calls()
        if not near_reset and search_calls_used(db, provider, on=on) + units > (
                settings.youtube_search_daily_calls - reserve):
            return False
    used = search_purpose_used(db, provider, purpose, on=on)
    if used + units <= search_purpose_budget(purpose):
        return True            # within the purpose's own allocation
    return near_reset or not others_have_backlog   # borrow only when idle elsewhere / near reset


def search_allocation_snapshot(db: Session, provider: str, *, on: date | None = None) -> dict:
    out = {}
    for p in SEARCH_PURPOSES:
        used = search_purpose_used(db, provider, p, on=on)
        budget = search_purpose_budget(p)
        out[p] = {"configured_fraction": _search_alloc_fraction(p), "budget": budget,
                  "used": used, "remaining": max(budget - used, 0),
                  "borrowed": max(used - budget, 0)}
    return out


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
    # Two INDEPENDENT quotas — reported separately, never summed into a misleading universal value.
    gen_used = general_used(db, provider, on=on)
    # Search-Queries is a CALL quota: report by call count (robust to lingering pre-fix 100-unit rows).
    search_calls = buckets[BUCKET_SEARCH]["requests"]
    # Reconciliation invariant: the general total equals exactly GENERAL_READ + VIDEO_STATS_BATCH units
    # (SEARCH and SEARCH:<purpose> sub-buckets are never added in). Diagnostics assert this holds.
    reconciles = gen_used == (buckets[BUCKET_GENERAL_READ]["used"] + buckets[BUCKET_VIDEO_BATCH]["used"])
    return {
        "provider": provider, "quota_date": day.isoformat(), "reset_tz": settings.youtube_quota_reset_tz,
        "model": "granular-independent-buckets-2026",
        "search_queries": {
            "used_calls": search_calls, "daily_quota_calls": settings.youtube_search_daily_calls,
            "remaining_calls": max(settings.youtube_search_daily_calls - search_calls, 0),
            "reserve_calls": search_reserve_calls(),
            "cost_per_call": YT_SEARCH_UNITS, "independent": True,
            "note": "accounted by CALL COUNT; lingering pre-fix 100-unit rows do not affect gating"},
        "general_pool": {
            "daily_quota_units": settings.youtube_daily_quota_units,
            "target_utilization": settings.youtube_quota_target_utilization,
            "usable_units": usable_units(), "reserve_units": reserve_units(),
            "used": gen_used, "remaining_usable": max(usable_units() - gen_used, 0)},
        "reconciles": reconciles,
        # legacy fields kept for existing readers — now the HONEST general-pool total (not the mixed sum)
        "daily_quota_units": settings.youtube_daily_quota_units,
        "target_utilization": settings.youtube_quota_target_utilization,
        "usable_units": usable_units(), "reserve_units": reserve_units(),
        "used_total": gen_used, "remaining_usable": max(usable_units() - gen_used, 0),
        "buckets": buckets,
        "search_allocation": search_allocation_snapshot(db, provider, on=on),
    }
