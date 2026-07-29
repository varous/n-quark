from api_gateway.config import LOCAL_DOWNSTREAM_SERVICES, Settings, detect_network_mode


def test_local_downstream_services_use_localhost() -> None:
    settings = Settings(network_mode="local")
    urls = settings.downstream_services.values()
    assert all("localhost" in url for url in urls)
    assert len(settings.downstream_services) == 9


def test_docker_downstream_services_use_service_hostnames() -> None:
    settings = Settings(network_mode="docker")
    assert settings.downstream_services["crawl"] == "http://crawl-service:8001"
    assert "localhost" not in settings.downstream_services["crawl"]


def test_detect_network_mode_defaults_to_local_outside_container() -> None:
    assert detect_network_mode() in ("local", "docker")


def test_local_service_ports_match_architecture() -> None:
    assert LOCAL_DOWNSTREAM_SERVICES["intelligence"] == "http://localhost:8009"
