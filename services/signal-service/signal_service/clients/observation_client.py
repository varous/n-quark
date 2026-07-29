from datetime import UTC, datetime
from typing import Any

import httpx

from signal_service.config import settings
from signal_service.schemas import NormalizedObservation


class ObservationServiceClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.observation_service_url).rstrip("/")

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
        results: list[dict[str, Any]] = []
        for observation in observations:
            results.append(await self.append_observation(observation))
        return results
