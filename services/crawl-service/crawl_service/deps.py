"""Dependency wiring for the scheduler + enrichment. Tests override these with in-process stubs."""

from crawl_service.capturer import HttpCapturer
from crawl_service.config import settings
from crawl_service.db import SessionLocal
from crawl_service.enrichment.clients import HttpGraphReader, HttpPageFetcher
from crawl_service.enrichment.service import EnrichmentService
from crawl_service.service import SchedulerService

_scheduler: SchedulerService | None = None
_enricher: EnrichmentService | None = None


def get_enricher() -> EnrichmentService:
    global _enricher
    if _enricher is None:
        page_fetcher = HttpPageFetcher() if settings.capture_enrichment_public_page_enabled else None
        _enricher = EnrichmentService(SessionLocal, HttpGraphReader(), page_fetcher, settings)
    return _enricher


def get_scheduler() -> SchedulerService:
    global _scheduler
    if _scheduler is None:
        _scheduler = SchedulerService(SessionLocal, HttpCapturer(), settings, enricher=get_enricher())
    return _scheduler
