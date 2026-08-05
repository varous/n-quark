"""Safe fetcher: SSRF protection, size/MIME rejection, redirect limits, streaming success."""

import httpx
import pytest

from media_service import fetcher
from media_service.fetcher import FetchBlocked, fetch, validate_url
from tests.conftest import png_bytes


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def _resolver_to(ip):
    def resolver(host, port):
        return [(2, 1, 6, "", (ip, 0))]
    return resolver


# ---- SSRF / scheme ---------------------------------------------------------------------------
def test_private_target_blocked():
    with pytest.raises(FetchBlocked) as e:
        validate_url("http://internal/x.png", resolver=_resolver_to("10.0.0.1"))
    assert e.value.error_class == fetcher.BLOCKED


def test_loopback_blocked():
    with pytest.raises(FetchBlocked):
        validate_url("http://localhost/x.png", resolver=_resolver_to("127.0.0.1"))


def test_public_target_allowed():
    assert validate_url("https://cdn.example.com/x.png", resolver=_resolver_to("93.184.216.34"))


def test_non_http_scheme_blocked():
    with pytest.raises(FetchBlocked):
        validate_url("file:///etc/passwd")
    with pytest.raises(FetchBlocked):
        validate_url("ftp://host/x")


# ---- fetch behaviour -------------------------------------------------------------------------
async def test_fetch_success():
    def handler(req):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=png_bytes(10, 10))
    async with _client(handler) as c:
        res = await fetch("https://cdn.example.com/a.png", max_bytes=10_000, timeout_seconds=5,
                          allowed_mime={"image/png"}, allow_private=True, client=c)
    assert res.ok and res.content_type == "image/png" and res.data is not None


async def test_too_large():
    def handler(req):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=png_bytes(10, 10, b"x" * 5000))
    async with _client(handler) as c:
        res = await fetch("https://cdn.example.com/a.png", max_bytes=100, timeout_seconds=5,
                          allowed_mime={"image/png"}, allow_private=True, client=c)
    assert res.status == fetcher.TOO_LARGE


async def test_unsupported_mime_header():
    def handler(req):
        return httpx.Response(200, headers={"content-type": "image/svg+xml"}, content=b"<svg/>")
    async with _client(handler) as c:
        res = await fetch("https://cdn.example.com/a.svg", max_bytes=10_000, timeout_seconds=5,
                          allowed_mime={"image/png"}, allow_private=True, client=c)
    assert res.status == fetcher.UNSUPPORTED_TYPE


async def test_invalid_content_bytes():
    def handler(req):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"not really a png")
    async with _client(handler) as c:
        res = await fetch("https://cdn.example.com/a.png", max_bytes=10_000, timeout_seconds=5,
                          allowed_mime={"image/png"}, allow_private=True, client=c)
    assert res.status == fetcher.INVALID_CONTENT


async def test_not_found():
    def handler(req):
        return httpx.Response(404)
    async with _client(handler) as c:
        res = await fetch("https://cdn.example.com/missing.png", max_bytes=10_000, timeout_seconds=5,
                          allowed_mime={"image/png"}, allow_private=True, client=c)
    assert res.status == fetcher.NOT_FOUND


async def test_redirect_limit():
    def handler(req):
        return httpx.Response(302, headers={"location": "https://cdn.example.com/next.png"})
    async with _client(handler) as c:
        res = await fetch("https://cdn.example.com/a.png", max_bytes=10_000, timeout_seconds=5,
                          allowed_mime={"image/png"}, redirect_limit=2, allow_private=True, client=c)
    assert res.status == fetcher.BLOCKED
