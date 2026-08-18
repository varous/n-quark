"""Dependency wiring for the scheduler + enrichment. Tests override these with in-process stubs."""

from crawl_service.capturer import HttpCapturer
from crawl_service.config import settings
from crawl_service.db import SessionLocal
from crawl_service.enrichment.clients import HttpGraphReader, HttpGraphWriter, HttpPageFetcher
from crawl_service.enrichment.pilot.service import PilotService
from crawl_service.enrichment.service import EnrichmentService
from crawl_service.entity_resolution.service import EntityResolutionService
from crawl_service.governance import GovernanceService
from crawl_service.reconciliation.probe import ProbeService
from crawl_service.reconciliation.service import ReconciliationService
from crawl_service.service import SchedulerService

_scheduler: SchedulerService | None = None
_enricher: EnrichmentService | None = None
_pilot: PilotService | None = None
_reconciler: ReconciliationService | None = None
_probe: ProbeService | None = None
_social = None
_social_interpreter = None
_entity_resolver: EntityResolutionService | None = None
_governance: GovernanceService | None = None


def get_governance_service() -> GovernanceService:
    global _governance
    if _governance is None:
        _governance = GovernanceService(SessionLocal, HttpGraphReader(), HttpGraphWriter(), settings)
    return _governance


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


def build_scheduler(session_factory=SessionLocal) -> SchedulerService:
    """Construct a fresh full-pipeline scheduler (enrichment + entity resolution + media hook).

    Used by the HTTP app (via get_scheduler), the run-to-completion worker, the bootstrap command and
    the continuous collector — one construction, no divergence."""
    from crawl_service.media_notifier import notify_media
    page_fetcher = HttpPageFetcher() if settings.capture_enrichment_public_page_enabled else None
    enricher = EnrichmentService(session_factory, HttpGraphReader(), page_fetcher, settings)
    entity_resolver = EntityResolutionService(session_factory, HttpGraphReader(), HttpGraphWriter(), settings)
    return SchedulerService(session_factory, HttpCapturer(), settings,
                            enricher=enricher, entity_resolver=entity_resolver,
                            media_notifier=notify_media)


def get_scheduler() -> SchedulerService:
    global _scheduler
    if _scheduler is None:
        _scheduler = build_scheduler()
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


def get_social_service() -> "SocialAcquisitionService":  # noqa: F821
    global _social
    if _social is None:
        from crawl_service.social import SocialAcquisitionService
        _social = SocialAcquisitionService(SessionLocal, settings)
    return _social


async def _existing_event_views():
    """Existing tracked-event EventViews (both reconciliation sources) that social evidence is matched
    against. Built from the SAME graph views the reconciliation service uses — no parallel source of
    truth. Failures degrade to a partial/empty set (social evidence then stands as a NEW hypothesis)."""
    recon = get_reconciliation_service()
    sources = ["boshow"] + ([settings.second_source_name] if settings.second_source_name else [])
    try:
        return await recon.source_event_views(sources)
    except Exception:  # noqa: BLE001 — never let projection break interpretation
        return []


def get_social_interpretation_service() -> "SocialInterpretationService":  # noqa: F821
    global _social_interpreter
    if _social_interpreter is None:
        from crawl_service.social_interpretation.service import SocialInterpretationService
        _social_interpreter = SocialInterpretationService(
            SessionLocal, settings, existing_views_provider=_existing_event_views)
    return _social_interpreter
