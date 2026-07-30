from fastapi import APIRouter, Request

from api_gateway.config import settings
from api_gateway.proxy import forward_request

router = APIRouter(tags=["proxy"])


def _target(service: str, resource: str, path: str) -> str:
    base = settings.downstream_services[service]
    url = f"{base}{resource}"
    if path:
        url = f"{url}/{path}"
    return url


@router.api_route(
    "/v1/observations",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
@router.api_route(
    "/v1/observations/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy_observations(request: Request, path: str = "") -> object:
    return await forward_request(_target("observation", "/v1/observations", path), request)


@router.api_route(
    "/v1/entities",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
@router.api_route(
    "/v1/entities/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy_entities(request: Request, path: str = "") -> object:
    return await forward_request(_target("entity", "/v1/entities", path), request)


@router.api_route(
    "/v1/signals",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
@router.api_route(
    "/v1/signals/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy_signals(request: Request, path: str = "") -> object:
    return await forward_request(_target("signal", "/v1/signals", path), request)


@router.api_route("/v1/graph", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@router.api_route("/v1/graph/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_graph(request: Request, path: str = "") -> object:
    return await forward_request(_target("graph", "/v1/graph", path), request)


@router.api_route("/v1/analytics", methods=["GET"])
@router.api_route("/v1/analytics/{path:path}", methods=["GET"])
async def proxy_analytics(request: Request, path: str = "") -> object:
    return await forward_request(_target("analytics", "/v1/analytics", path), request)
