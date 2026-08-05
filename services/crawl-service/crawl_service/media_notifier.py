"""Best-effort creative-observation notifier (Phase 4B).

After a successful capture, crawl-service tells media-service to observe the event's creative. media
resolves the asset reference from the canonical event node the capture wrote (`resolve_from_graph`) and
processes it best-effort. This call must never fail the capture — the scheduler wraps it in try/except,
and the HTTP timeout is short. crawl does not fetch or hash images itself.
"""

from datetime import datetime
from typing import Any

import httpx

from crawl_service.config import settings


async def notify_media(*, source: str, source_record_id: str, canonical_event_id: str,
                       now: datetime) -> dict[str, Any]:
    payload = {
        "canonical_event_id": canonical_event_id,
        "source": source,
        "source_record_id": source_record_id,
        "observed_at": now.isoformat(),
        "authoritative": True,       # a successful capture — absence here means the ref is gone
        "resolve_from_graph": True,  # media reads image_url from the event node
    }
    async with httpx.AsyncClient(timeout=settings.capture_http_timeout_seconds) as client:
        resp = await client.post(
            f"{settings.media_service_url}/v1/internal/media/observe", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return {"outcome": "MEDIA_OBSERVED", "transitions": data.get("transitions", []),
            "fetch_status": data.get("fetch_status"), "media_asset_id": data.get("media_asset_id")}
