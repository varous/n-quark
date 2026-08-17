"""Lazy singletons for the admin BFF (tests override these)."""

from api_gateway.admin.audit import AuditStore
from api_gateway.admin.decisions import DecisionStore
from api_gateway.admin.catalog import CatalogAdminService
from api_gateway.admin.demand import DemandAdminService
from api_gateway.admin.gateway_client import DownstreamGateway
from api_gateway.admin.service import AdminService
from api_gateway.admin.social import SocialAdminService
from api_gateway.admin.watchlist import WatchlistAdminService

_service: AdminService | None = None
_demand: DemandAdminService | None = None
_catalog: CatalogAdminService | None = None
_watchlist: WatchlistAdminService | None = None
_social: SocialAdminService | None = None
_audit: AuditStore | None = None
_decisions: DecisionStore | None = None


def get_admin_service() -> AdminService:
    global _service
    if _service is None:
        _service = AdminService(DownstreamGateway())
    return _service


def get_demand_service() -> DemandAdminService:
    global _demand
    if _demand is None:
        _demand = DemandAdminService(DownstreamGateway())
    return _demand


def get_catalog_service() -> CatalogAdminService:
    global _catalog
    if _catalog is None:
        _catalog = CatalogAdminService(DownstreamGateway())
    return _catalog


def get_watchlist_service() -> WatchlistAdminService:
    global _watchlist
    if _watchlist is None:
        _watchlist = WatchlistAdminService(DownstreamGateway())
    return _watchlist


def get_social_service() -> SocialAdminService:
    global _social
    if _social is None:
        _social = SocialAdminService(DownstreamGateway())
    return _social


def get_audit_store() -> AuditStore:
    global _audit
    if _audit is None:
        _audit = AuditStore()
    return _audit


def get_decision_store() -> DecisionStore:
    global _decisions
    if _decisions is None:
        _decisions = DecisionStore()
    return _decisions
