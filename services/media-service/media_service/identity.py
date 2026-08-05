"""Deterministic asset identity (Phase 4B).

Identity order: exact content SHA-256, then normalized source URL. Content identity is
source-independent — identical bytes from different URLs are one asset. A perceptual hash column
exists for a future near-identical match, but is left unset here to avoid heavy image dependencies.
Assets are never merged on filename alone.
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

_DEFAULT_PORTS = {"http": "80", "https": "443"}


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_url(url: str) -> str:
    """Canonicalize a URL for dedup/identity: lowercase scheme+host, drop default port, drop
    fragment. Path and query are preserved (they can select a different asset)."""
    url = (url or "").strip()
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    netloc = host
    if parts.port and str(parts.port) != _DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{parts.port}"
    if parts.username:
        cred = parts.username + (f":{parts.password}" if parts.password else "")
        netloc = f"{cred}@{netloc}"
    return urlunsplit((scheme, netloc, parts.path, parts.query, ""))


def identity_key(asset_id: str | None, normalized_url: str) -> str:
    """The comparable identity for transition detection: content when known, else the normalized URL."""
    return asset_id if asset_id else f"url:{normalized_url}"
