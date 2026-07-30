from analytics_service.graph_client import GraphServiceClient


def get_graph_client() -> GraphServiceClient:
    """Injectable graph client so tests can override it with a stub."""
    return GraphServiceClient()
