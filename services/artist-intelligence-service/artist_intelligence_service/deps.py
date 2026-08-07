from artist_intelligence_service.scheduler import DemandScheduler
from artist_intelligence_service.service import DemandService


def get_service() -> DemandService:
    return DemandService()


def get_scheduler() -> DemandScheduler:
    return DemandScheduler()
