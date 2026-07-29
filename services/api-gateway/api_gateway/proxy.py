import httpx
from fastapi import Request, Response

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}


async def forward_request(target_url: str, request: Request) -> Response:
    """Proxy an HTTP request to a downstream service."""
    url = target_url
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }
    body = await request.body()

    async with httpx.AsyncClient(timeout=30.0) as client:
        downstream = await client.request(
            request.method,
            url,
            content=body,
            headers=headers,
        )

    response_headers = {
        key: value
        for key, value in downstream.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }
    return Response(
        content=downstream.content,
        status_code=downstream.status_code,
        headers=response_headers,
        media_type=downstream.headers.get("content-type"),
    )
