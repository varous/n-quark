from fastapi import APIRouter, Depends, HTTPException, status

from analytics_service.deps import get_graph_client
from analytics_service.graph_client import GraphServiceClient
from analytics_service.schemas import ArtistDemandResponse, RegionIntelligenceResponse
from analytics_service.scoring import artist_demand, region_intelligence

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


@router.get(
    "/artists/{canonical_id}",
    response_model=ArtistDemandResponse,
    summary="Deterministic live-event demand score for an artist",
)
async def artist_demand_score(
    canonical_id: str, graph: GraphServiceClient = Depends(get_graph_client)
) -> ArtistDemandResponse:
    node = await graph.get_node(canonical_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not in graph")
    # Geographic demand = the regions this artist is STRONG_IN.
    strong = await graph.neighbors(canonical_id, direction="out", relationship="strong_in")
    regions = [
        n["node"]["properties"].get("display_name") or n["node"]["id"]
        for n in strong
        if n.get("node", {}).get("type") == "region"
    ]
    result = artist_demand(canonical_id, node.get("properties", {}), regions)
    return ArtistDemandResponse(**result.__dict__)


@router.get(
    "/regions/{region_id}",
    response_model=RegionIntelligenceResponse,
    summary="Demand-vs-supply intelligence for a region",
)
async def region_intelligence_readout(
    region_id: str, graph: GraphServiceClient = Depends(get_graph_client)
) -> RegionIntelligenceResponse:
    node = await graph.get_node(region_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Region not in graph")
    incoming = await graph.neighbors(region_id, direction="in")
    # Demand: artists STRONG_IN the region. Supply: events IN_REGION it.
    artists = [
        n["node"]["properties"].get("display_name") or n["node"]["id"]
        for n in incoming
        if n.get("relationship") == "STRONG_IN" and n.get("node", {}).get("type") == "artist"
    ]
    events = [n["node"] for n in incoming if n.get("relationship") == "IN_REGION"]
    result = region_intelligence(region_id, artists, events)
    return RegionIntelligenceResponse(
        region_id=result.region_id,
        demand_signals=result.demand_signals,
        supply_signals=result.supply_signals,
        demanding_artists=result.demanding_artists,
        events=result.events,
        avg_fill_ratio=result.avg_fill_ratio,
        avg_price=result.avg_price,
        demand_supply_ratio=result.demand_supply_ratio,
        verdict=result.verdict,
        explanation=result.explanation,
    )
