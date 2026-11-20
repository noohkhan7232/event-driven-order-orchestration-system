from pathlib import Path

import yaml


def test_compose_gates_applications_on_migrations_and_healthy_dependencies():
    config = yaml.safe_load(Path("docker-compose.yml").read_text())
    services = config["services"]
    assert services["migrate"]["command"] == "alembic upgrade head"
    for name in ["api", "worker", "scheduler", "outbox-relay"]:
        dependencies = services[name]["depends_on"]
        assert dependencies["migrate"]["condition"] == "service_completed_successfully"
        assert dependencies["postgres"]["condition"] == dependencies["rabbitmq"]["condition"] == "service_healthy"
    assert services["outbox-relay"]["command"] == "python -m app.workers.outbox_relay"


def test_compose_api_port_is_overridable():
    config = yaml.safe_load(Path("docker-compose.yml").read_text())
    assert "${API_PORT:-8000}" in config["services"]["api"]["ports"][0]
