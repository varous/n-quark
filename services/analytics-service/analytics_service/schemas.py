from pydantic import BaseModel, Field


class ArtistDemandResponse(BaseModel):
    entity_id: str
    demand_score: float
    components: dict[str, float]
    weights: dict[str, float]
    strongest_markets: list[str]
    missing_signals: list[str] = Field(default_factory=list)
    explanation: str


class RegionIntelligenceResponse(BaseModel):
    region_id: str
    demand_signals: int
    supply_signals: int
    demanding_artists: list[str]
    events: list[str]
    avg_fill_ratio: float | None = None
    avg_price: float | None = None
    demand_supply_ratio: float
    verdict: str
    explanation: str
