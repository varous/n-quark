"""Safe, bounded asset fetcher (Phase 4B).

HTTP/HTTPS only, streamed, size-capped, MIME-validated, with a redirect limit and SSRF protection
(private/loopback/link-local/reserved targets are blocked, re-checked on every redirect hop). It does
NOT bypass access controls, CAPTCHAs, authentication or anti-bot restrictions. A fetch failure is
classified, never silently swallowed, and never erases prior asset state (the caller decides).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass

import httpx

from media_service.metadata import sniff_format

# fetch result classification
FETCHED = "FETCHED"
NOT_FOUND = "NOT_FOUND"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
BLOCKED = "BLOCKED"
INVALID_CONTENT = "INVALID_CONTENT"
TOO_LARGE = "TOO_LARGE"
TIMEOUT = "TIMEOUT"
UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"


class FetchBlocked(Exception):
    def __init__(self, error_class: str, detail: str = "") -> None:
        super().__init__(detail or error_class)
        self.error_class = error_class


@dataclass(frozen=True)
class FetchResult:
    status: str
    http_status: int | None = None
    content_type: str | None = None
    data: bytes | None = None
    final_url: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == FETCHED


def _ip_is_disallowed(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
            or addr.is_multicast or addr.is_unspecified)


def validate_url(url: str, *, allow_private: bool = False, resolver=socket.getaddrinfo) -> str:
    """Raise FetchBlocked unless `url` is an http(s) URL that resolves only to public addresses."""
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchBlocked(BLOCKED, f"scheme {parsed.scheme!r} not allowed")
    host = parsed.host
    if not host:
        raise FetchBlocked(BLOCKED, "missing host")
    if allow_private:
        return host
    try:
        infos = resolver(host, None)
    except socket.gaierror as exc:
        raise FetchBlocked(SOURCE_UNAVAILABLE, f"dns failure: {exc}") from exc
    ips = {info[4][0] for info in infos}
    if not ips:
        raise FetchBlocked(SOURCE_UNAVAILABLE, "no addresses")
    for ip in ips:
        if _ip_is_disallowed(ip):
            raise FetchBlocked(BLOCKED, f"target resolves to non-public address {ip}")
    return host


async def fetch(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float,
    allowed_mime: set[str],
    redirect_limit: int = 3,
    allow_private: bool = False,
    client: httpx.AsyncClient | None = None,
    resolver=socket.getaddrinfo,
) -> FetchResult:
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False)
    current = url
    try:
        for _ in range(redirect_limit + 1):
            try:
                validate_url(current, allow_private=allow_private, resolver=resolver)
            except FetchBlocked as exc:
                return FetchResult(status=exc.error_class, error=str(exc), final_url=current)
            try:
                async with client.stream("GET", current) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location")
                        if not loc:
                            return FetchResult(BLOCKED, resp.status_code, error="redirect without location")
                        current = str(httpx.URL(current).join(loc))
                        continue
                    if resp.status_code == 404:
                        return FetchResult(NOT_FOUND, 404, final_url=current)
                    if resp.status_code >= 400:
                        return FetchResult(SOURCE_UNAVAILABLE, resp.status_code, final_url=current)
                    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                    if ctype and ctype not in allowed_mime:
                        return FetchResult(UNSUPPORTED_TYPE, resp.status_code, content_type=ctype,
                                           final_url=current)
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > max_bytes:
                            return FetchResult(TOO_LARGE, resp.status_code, content_type=ctype,
                                               final_url=current)
                    data = bytes(buf)
                    sniffed = sniff_format(data)
                    if sniffed is None:
                        return FetchResult(INVALID_CONTENT, resp.status_code, content_type=ctype,
                                           final_url=current, error="bytes are not a recognized image")
                    if sniffed[0] not in allowed_mime:
                        return FetchResult(UNSUPPORTED_TYPE, resp.status_code, content_type=sniffed[0],
                                           final_url=current)
                    return FetchResult(FETCHED, resp.status_code, content_type=sniffed[0], data=data,
                                       final_url=current)
            except httpx.TimeoutException as exc:
                return FetchResult(TIMEOUT, error=str(exc), final_url=current)
            except httpx.HTTPError as exc:
                return FetchResult(SOURCE_UNAVAILABLE, error=str(exc), final_url=current)
        return FetchResult(BLOCKED, error="redirect limit exceeded", final_url=current)
    finally:
        if owns_client:
            await client.aclose()
