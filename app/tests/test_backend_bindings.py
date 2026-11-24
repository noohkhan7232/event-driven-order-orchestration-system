from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize(
    "service, variable, internal",
    [
        ("postgres", "POSTGRES_PORT", 5432),
        ("redis", "REDIS_PORT", 6379),
        ("rabbitmq", "RABBITMQ_PORT", 5672),
        ("rabbitmq", "RABBITMQ_MANAGEMENT_PORT", 15672),
    ],
)
def test_backend_bindings_are_loopback_only_with_overridable_host_ports(service, variable, internal):
    services = yaml.safe_load(Path("docker-compose.yml").read_text())["services"]
    assert f"127.0.0.1:${{{variable}:-{internal}}}:{internal}" in services[service]["ports"]
