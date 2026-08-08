from api_gateway.config import LOCAL_DOWNSTREAM_SERVICES, Settings, detect_network_mode


def test_local_downstream_services_use_localhost() -> None:
    settings = Settings(network_mode="local")
    urls = settings.downstream_services.values()
    assert all("localhost" in url for url in urls)
    # 9 core services + artist-intelligence-service (Phase 5A.2 demand BFF downstream)
    assert len(settings.downstream_services) == 10
    assert LOCAL_DOWNSTREAM_SERVICES["artist_intelligence"] == "http://localhost:8010"


def test_docker_downstream_services_use_service_hostnames() -> None:
    settings = Settings(network_mode="docker")
    assert settings.downstream_services["crawl"] == "http://crawl-service:8001"
    assert "localhost" not in settings.downstream_services["crawl"]


def test_detect_network_mode_defaults_to_local_outside_container() -> None:
    assert detect_network_mode() in ("local", "docker")


def test_local_service_ports_match_architecture() -> None:
    assert LOCAL_DOWNSTREAM_SERVICES["intelligence"] == "http://localhost:8009"


def test_upstream_override_wins_and_is_scoped() -> None:
    # Deploy targets (e.g. Fly flycast) override individual upstreams; others stay on the base map.
    settings = Settings(network_mode="docker", graph_service_url="http://nquark-graph-service.flycast/")
    assert settings.downstream_services["graph"] == "http://nquark-graph-service.flycast"  # trailing / stripped
    assert settings.downstream_services["signal"] == "http://signal-service:8003"  # untouched


def test_no_overrides_matches_base_map() -> None:
    assert Settings(network_mode="docker").downstream_services["graph"] == "http://graph-service:8006"
