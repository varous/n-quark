"""Lazy singletons for the admin BFF (tests override these)."""

from api_gateway.admin.audit import AuditStore
from api_gateway.admin.decisions import DecisionStore
from api_gateway.admin.demand import DemandAdminService
from api_gateway.admin.gateway_client import DownstreamGateway
from api_gateway.admin.service import AdminService

_service: AdminService | None = None
_demand: DemandAdminService | None = None
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
