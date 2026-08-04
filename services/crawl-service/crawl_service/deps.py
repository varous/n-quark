"""Dependency wiring for the scheduler + enrichment. Tests override these with in-process stubs."""

from crawl_service.capturer import HttpCapturer
from crawl_service.config import settings
from crawl_service.db import SessionLocal
from crawl_service.enrichment.clients import HttpGraphReader, HttpGraphWriter, HttpPageFetcher
from crawl_service.enrichment.pilot.service import PilotService
from crawl_service.enrichment.service import EnrichmentService
from crawl_service.entity_resolution.service import EntityResolutionService
from crawl_service.reconciliation.probe import ProbeService
from crawl_service.reconciliation.service import ReconciliationService
from crawl_service.service import SchedulerService

_scheduler: SchedulerService | None = None
_enricher: EnrichmentService | None = None
_pilot: PilotService | None = None
_reconciler: ReconciliationService | None = None
_probe: ProbeService | None = None
_entity_resolver: EntityResolutionService | None = None


def get_enricher() -> EnrichmentService:
    global _enricher
    if _enricher is None:
        page_fetcher = HttpPageFetcher() if settings.capture_enrichment_public_page_enabled else None
        _enricher = EnrichmentService(SessionLocal, HttpGraphReader(), page_fetcher, settings)
    return _enricher


def get_entity_resolution_service() -> EntityResolutionService:
    global _entity_resolver
    if _entity_resolver is None:
        _entity_resolver = EntityResolutionService(
            SessionLocal, HttpGraphReader(), HttpGraphWriter(), settings)
    return _entity_resolver


def get_scheduler() -> SchedulerService:
    global _scheduler
    if _scheduler is None:
        _scheduler = SchedulerService(SessionLocal, HttpCapturer(), settings,
                                      enricher=get_enricher(),
                                      entity_resolver=get_entity_resolution_service())
    return _scheduler


def get_pilot_service() -> PilotService:
    global _pilot
    if _pilot is None:
        _pilot = PilotService(SessionLocal, HttpGraphReader(), get_enricher(), settings)
    return _pilot


def get_reconciliation_service() -> ReconciliationService:
    global _reconciler
    if _reconciler is None:
        _reconciler = ReconciliationService(SessionLocal, HttpGraphReader(), get_enricher(), settings)
    return _reconciler


def get_probe_service() -> ProbeService:
    global _probe
    if _probe is None:
        _probe = ProbeService(settings.signal_service_url)
    return _probe
