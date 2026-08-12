"""Phase 5B.2 — deterministic YouTube content-movement engine.

Movement is age-normalised, evidence-gated, and explainable — never a single fused score. These tests
exercise velocity/acceleration derivation, evidence sufficiency (INSUFFICIENT_HISTORY), the deterministic
states, the age-normalised baseline, owned-vs-ecosystem distinctness, and availability semantics.
"""

from datetime import UTC, datetime, timedelta

from artist_intelligence_service import movement as mv
from artist_intelligence_service import videos as vids
from artist_intelligence_service.models import YouTubeVideo
from artist_intelligence_service.providers.base import YT_VIDEO_VIEWS
from tests.conftest import seed_obs

NOW = datetime(2026, 8, 12, 12, 0, 0, tzinfo=UTC)


def series(points):
    """[(hours_ago, value)] -> ascending [(observed_at, value)]."""
    return sorted([(NOW - timedelta(hours=h), float(v)) for h, v in points], key=lambda t: t[0])


def video(vid, *, age_h=15.0, rel="OWNED_CONTENT", channel="UCown"):
    return YouTubeVideo(video_id=vid, channel_id=channel, canonical_artist_id="artist:a",
                        relationship_type=rel, availability_state="AVAILABLE", title=vid,
                        published_at=NOW - timedelta(hours=age_h), tracking_status="ACTIVE",
                        first_seen_at=NOW, created_at=NOW, updated_at=NOW)


BASE = {"<24h": [100.0, 100.0, 100.0]}   # a comparable-age baseline cohort (median 100/h)


# ---- 6/7. velocity + insufficient history ------------------------------------------------------
def test_single_observation_is_insufficient():
    out = mv.video_movement(video("v1"), views=series([(0, 1000)]), comments=None,
                            baseline_values=[100.0, 100.0, 100.0], bucket_label="<24h", now=NOW)
    assert out["classification"] == mv.INSUFFICIENT_HISTORY
    assert out["observation_count"] == 1


def test_separated_observations_produce_velocity():
    v = mv.window_velocity(series([(6, 600), (3, 900), (0, 1200)]), NOW, window_hours=6.0)
    assert v is not None and abs(v[0] - 100.0) < 1e-6      # (1200-600)/6


# ---- 8/9/10. acceleration, age-normalised baseline, breakout -----------------------------------
def test_breakout_requires_ratio_and_acceleration():
    # current 500/h (5× baseline), prior 300/h (accel 1.67) → BREAKOUT_CANDIDATE
    out = mv.video_movement(video("vbreak"),
                            views=series([(12, 3000), (9, 3900), (6, 4800), (3, 6300), (0, 7800)]),
                            comments=None, baseline_values=[100.0, 100.0, 100.0], bucket_label="<24h", now=NOW)
    assert out["classification"] == mv.BREAKOUT_CANDIDATE
    sv = out["supporting_values"]
    assert sv["velocity_ratio_vs_baseline"] >= 3.0 and sv["acceleration"] >= 1.3
    assert out["comparison_cohort"] == "owned videos aged <24h"
    assert out["baseline_sample_size"] == 3


def test_rising_without_acceleration():
    out = mv.video_movement(video("vrise"),
                            views=series([(12, 0), (9, 540), (6, 1080), (3, 1620), (0, 2160)]),
                            comments=None, baseline_values=[100.0, 100.0, 100.0], bucket_label="<24h", now=NOW)
    assert out["classification"] == mv.RISING                 # 1.8× baseline, flat acceleration


def test_normal_matches_baseline():
    out = mv.video_movement(video("vnorm"),
                            views=series([(12, 0), (9, 300), (6, 600), (3, 900), (0, 1200)]),
                            comments=None, baseline_values=[100.0, 100.0, 100.0], bucket_label="<24h", now=NOW)
    assert out["classification"] == mv.NORMAL


# ---- 11. cooling requires prior movement -------------------------------------------------------
def test_cooling_requires_prior_movement():
    # prior 500/h, current 100/h → decelerating from real prior movement → COOLING
    out = mv.video_movement(video("vcool"),
                            views=series([(12, 0), (9, 1500), (6, 3000), (3, 3300), (0, 3600)]),
                            comments=None, baseline_values=[100.0, 100.0, 100.0], bucket_label="<24h", now=NOW)
    assert out["classification"] == mv.COOLING
    assert out["supporting_values"]["acceleration"] <= 0.6


# ---- baseline sufficiency ----------------------------------------------------------------------
def test_no_comparable_cohort_is_not_classified():
    out = mv.video_movement(video("vlonely"),
                            views=series([(12, 0), (6, 3000), (0, 6000)]),
                            comments=None, baseline_values=[100.0], bucket_label="<24h", now=NOW)  # only 1 sample
    assert out["classification"] == mv.INSUFFICIENT_HISTORY
    assert out["evidence_state"] == "NO_BASELINE"


def test_age_normalised_baseline_buckets_separately(db):
    # a young and an old video land in different age buckets (never cross-compared)
    for i in range(3):
        _seed_series(db, f"y{i}", age_h=10, points=[(12, 0), (6, 600), (0, 1200)])
    _seed_series(db, "old", age_h=800, points=[(12, 0), (6, 600), (0, 1200)])
    views_by, _ = mv._load_video_series(db, "artist:a")
    videos = vids.videos_for_artist(db, "artist:a", active_only=False)
    buckets = mv.build_baselines(db, "artist:a", videos, NOW, views_by)
    assert "<24h" in buckets and len(buckets["<24h"]) == 3
    assert any(k.startswith(">") or k == "7-30d" or k == ">30d" for k in buckets)  # old in its own bucket


# ---- 12. no universal score --------------------------------------------------------------------
def test_no_fused_virality_score():
    out = mv.video_movement(video("vx"), views=series([(12, 0), (6, 600), (0, 1200)]),
                            comments=None, baseline_values=[100.0, 100.0, 100.0], bucket_label="<24h", now=NOW)
    for banned in ("score", "virality", "rank", "index"):
        assert banned not in json_keys(out)


def json_keys(d, acc=None):
    acc = acc if acc is not None else set()
    if isinstance(d, dict):
        for k, v in d.items():
            acc.add(str(k).lower())
            json_keys(v, acc)
    return acc


# ---- integration through artist_movement + registry --------------------------------------------
def _seed_series(db, vid, *, age_h, points, rel="OWNED_CONTENT", channel="UCown"):
    vids.upsert_video(db, video_id=vid, channel_id=channel, canonical_artist_id="artist:a",
                      published_at=NOW - timedelta(hours=age_h), title=vid, now=NOW)
    v = db.get(YouTubeVideo, vid)
    v.relationship_type = rel
    for h, val in points:
        seed_obs(db, artist="artist:a", provider="YOUTUBE", metric=YT_VIDEO_VIEWS, value=float(val),
                 observed_at=NOW - timedelta(hours=h), scope_type="CONTENT", scope_id=vid,
                 bucket=f"{vid}:{h}")
    db.flush()


def test_duplicate_registration_is_idempotent(db):
    vids.upsert_video(db, video_id="dup", channel_id="UCown", canonical_artist_id="artist:a",
                      title="Dup", now=NOW)
    vids.upsert_video(db, video_id="dup", channel_id="UCown", canonical_artist_id="artist:a",
                      title="Dup", now=NOW)
    from sqlalchemy import func, select
    assert db.execute(select(func.count()).select_from(YouTubeVideo)).scalar_one() == 1


def test_owned_and_ecosystem_are_distinct(db):
    for i in range(3):
        _seed_series(db, f"o{i}", age_h=12, points=[(12, 0), (6, 600), (0, 1200)])
    _seed_series(db, "eco1", age_h=12, rel="ECOSYSTEM_CONTENT", channel="UCfestival",
                 points=[(12, 0), (6, 3000), (0, 6000)])
    out = mv.artist_movement(db, "artist:a", now=NOW)
    assert out["videos_considered"] == 4
    owned = mv.artist_movement(db, "artist:a", now=NOW, relationship="OWNED_CONTENT")
    assert owned["videos_considered"] == 3       # ecosystem excluded when filtered


def test_not_found_content_preserves_history(db):
    _seed_series(db, "gone", age_h=12, points=[(12, 0), (6, 600), (0, 1200)])
    v = db.get(YouTubeVideo, "gone")
    v.availability_state = "NOT_FOUND"           # marked gone…
    db.flush()
    from artist_intelligence_service import reads
    # …but its observation history is intact
    assert len(reads.content_series(db, "artist:a", YT_VIDEO_VIEWS)["gone"]) == 3


# ---- Artist Data Coverage (spec #22): collected vs unavailable vs insufficient vs zero ---------
class _FakeCrawlReg:
    async def canonical_artist_registered(self, cid):
        return True


class _FakeGraphEmpty:
    async def neighbors(self, node_id, *, direction="both", relationship=None):
        return []


async def test_data_coverage_distinguishes_states(db):
    import asyncio  # noqa: F401 — marker; runner handles async
    from artist_intelligence_service import coverage
    from artist_intelligence_service import identity as idlib
    from artist_intelligence_service.models import ArtistExternalIdentity
    from artist_intelligence_service.service import DemandService
    # a verified YouTube channel + one owned video with only 1 observation (insufficient history)
    db.add(ArtistExternalIdentity(
        id=idlib.new_id("YOUTUBE", "CHANNEL_ID", "UCown"), canonical_artist_id="artist:a",
        provider="YOUTUBE", identity_type="CHANNEL_ID", provider_id="UCown", status="RESOLVED",
        display_name="A", canonical_url="https://youtube.com/channel/UCown", confidence=1.0,
        first_seen_at=NOW, last_verified_at=NOW, created_at=NOW, updated_at=NOW))
    _seed_series(db, "one", age_h=5, points=[(0, 1000)])   # single obs → insufficient history
    db.flush()
    out = await coverage.artist_data_coverage(
        db, "artist:a", crawl=_FakeCrawlReg(), graph=_FakeGraphEmpty(), svc=DemandService())
    assert out["identity"]["youtube_identity"]["state"] == "VERIFIED"          # collected
    assert out["youtube"]["state"] == "COLLECTED"
    assert out["youtube"]["owned_videos_tracked"] == 1
    assert out["youtube"]["insufficient_history_videos"] == 1                  # insufficient
    assert out["live_activity"]["state"] == "ZERO_OBSERVED"                    # collected, truly zero
    assert out["demand"]["google_trends"]["state"] == "UNAVAILABLE"            # unavailable
