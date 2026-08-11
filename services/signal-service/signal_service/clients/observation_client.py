from datetime import UTC, datetime
from typing import Any

import httpx

from signal_service.config import settings
from signal_service.schemas import NormalizedObservation


class ObservationServiceClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.observation_service_url).rstrip("/")

    async def ping(self) -> tuple[bool, str]:
        """Liveness probe of the observation store (a HARD dependency of the capture ingest path).

        Returns ``(reachable, detail)``. ``detail`` carries the failure reason (e.g. a DNS error when
        the service is not deployed) so readiness responses can name the actual cause."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/health")
                response.raise_for_status()
            return True, "ok"
        except Exception as exc:  # noqa: BLE001 — any failure means "not ready"; report why, don't raise
            return False, f"{type(exc).__name__}: {exc}"

    async def append_observation(self, observation: NormalizedObservation) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self.base_url}/v1/observations",
                json=observation.to_payload(),
            )
            response.raise_for_status()
            return response.json()

    async def append_observations(
        self,
        observations: list[NormalizedObservation],
    ) -> list[dict[str, Any]]:
        """Append many observations in one request via the bulk endpoint."""
        if not observations:
            return []
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{self.base_url}/v1/observations/bulk",
                json={"observations": [obs.to_payload() for obs in observations]},
            )
            response.raise_for_status()
            return response.json()["observations"]
