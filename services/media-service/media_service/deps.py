"""Lazy singletons for media-service (tests override these)."""

from media_service import fetcher as fetchmod
from media_service.config import settings
from media_service.db import SessionLocal
from media_service.graph_client import GraphClient
from media_service.reads import MediaReads
from media_service.service import MediaService
from media_service.storage import ContentAddressedStore

_service: MediaService | None = None
_reads: MediaReads | None = None


async def _default_fetch(url: str):
    return await fetchmod.fetch(
        url,
        max_bytes=settings.media_max_bytes,
        timeout_seconds=settings.media_fetch_timeout_seconds,
        allowed_mime=settings.allowed_mime_set,
        redirect_limit=settings.media_redirect_limit,
        allow_private=settings.media_allow_private_networks,
    )


def get_media_service() -> MediaService:
    global _service
    if _service is None:
        _service = MediaService(
            session_factory=SessionLocal,
            fetch_fn=_default_fetch,
            store=ContentAddressedStore(settings.media_storage_dir,
                                        enabled=settings.media_storage_enabled,
                                        max_bytes=settings.media_max_bytes),
            graph=GraphClient(),
            cfg=settings,
        )
    return _service


def get_media_reads() -> MediaReads:
    global _reads
    if _reads is None:
        _reads = MediaReads(SessionLocal)
    return _reads
