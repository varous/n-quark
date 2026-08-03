"""Dependency wiring for the scheduler. Tests override get_scheduler with a stub-capturer service."""

from crawl_service.capturer import HttpCapturer
from crawl_service.config import settings
from crawl_service.db import SessionLocal
from crawl_service.service import SchedulerService

_scheduler: SchedulerService | None = None


def get_scheduler() -> SchedulerService:
    global _scheduler
    if _scheduler is None:
        _scheduler = SchedulerService(SessionLocal, HttpCapturer(), settings)
    return _scheduler
