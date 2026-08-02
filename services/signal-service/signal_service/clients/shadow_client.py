"""Client for graph-service's internal Shadow Ledger observe endpoint (Phase 1).

Best-effort: recording commercial state must never break ingestion. Callers wrap this in the same
try/except pattern used for the graph projection.
"""

from typing import Any

import httpx

from signal_service.config import settings


class ShadowLedgerClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.graph_service_url).rstrip("/")

    async def observe(
        self,
        *,
        canonical_event_id: str,
        source_id: str,
        commercial_state: dict[str, Any],
        source_record_id: str | None = None,
        observation_id: str | None = None,
        provenance: dict[str, Any] | None = None,
        epistemic_status: str = "observed_public_state",
    ) -> dict[str, Any]:
        payload = {
            "source_id": source_id,
            "source_record_id": source_record_id,
            "observation_id": observation_id,
            "epistemic_status": epistemic_status,
            "provenance": provenance or {},
            **commercial_state,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self.base_url}/v1/internal/events/{canonical_event_id}/shadow-ledger/observe",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
