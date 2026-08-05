"""Phase 4B — the best-effort media-observation hook must never fail the capture."""

from datetime import UTC, datetime

from crawl_service.config import settings
from crawl_service.service import SchedulerService


def _svc(notifier):
    return SchedulerService(session_factory=None, capturer=None, config=settings,
                            media_notifier=notifier)


async def test_media_hook_failure_is_isolated():
    async def boom(**kwargs):
        raise RuntimeError("media down")

    res = await _svc(boom)._run_media_observation("boshow", "sr1", "event:a", datetime.now(UTC))
    assert res["outcome"] == "MEDIA_OBSERVATION_FAILED" and "media down" in res["error"]


async def test_media_hook_success_passes_through():
    async def ok(**kwargs):
        return {"outcome": "MEDIA_OBSERVED", "transitions": ["MEDIA_FIRST_SEEN"]}

    res = await _svc(ok)._run_media_observation("boshow", "sr1", "event:a", datetime.now(UTC))
    assert res["transitions"] == ["MEDIA_FIRST_SEEN"]
