from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_production_compose_separates_migrator_from_rls_runtime_role() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())
    services = compose["services"]

    # Production consumes managed RDS. The compose fallback must never start a
    # database container that silently collapses migrator and runtime roles.
    assert "postgres" not in services

    migrator_url = services["migrate"]["environment"]["DATABASE_URL"]
    assert "DATABASE_MIGRATOR_URL" in migrator_url
    assert services["migrate"]["env_file"].endswith(".env.migration}")
    assert services["migrate"]["command"].startswith("alembic upgrade")

    for service_name in ("api", "worker", "beat"):
        service = services[service_name]
        runtime_url = service["environment"]["DATABASE_URL"]
        assert "DATABASE_URL" in runtime_url
        assert "DATABASE_MIGRATOR_URL" not in runtime_url
        assert service["env_file"].endswith(".env.runtime}")
        assert service["depends_on"]["migrate"]["condition"] == "service_completed_successfully"

    assert services["api"]["command"].startswith("uvicorn ")
