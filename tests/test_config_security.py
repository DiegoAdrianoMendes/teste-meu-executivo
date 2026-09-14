from pathlib import Path

import pytest
from dotenv import dotenv_values
from pydantic import ValidationError

from app.core.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"


def _settings_overrides(
    secret_key: str,
    *,
    production: bool,
    integration_secret_key: str = TEST_INTEGRATION_SECRET_KEY,
) -> dict:
    return {
        "_env_file": None,
        "APP_ENV": "production" if production else "development",
        "ENVIRONMENT": "",
        "SECRET_KEY": secret_key,
        "INTEGRATION_SECRET_ENCRYPTION_KEY": integration_secret_key,
        "FRONTEND_URL": "https://app.example.test",
        "BLACKBOX_FRONTEND_URL": "https://blackbox.example.test",
        "CORS_ORIGINS": [
            "https://app.example.test",
            "https://blackbox.example.test",
        ],
        "KLM_RUNTIME_MODE": "production" if production else "local_simulation",
        "KLM_EXTERNAL_ACTIONS_ENABLED": False,
        "KLM_AWS_ENABLED": False,
        "KLM_ONLINE_APPROVED": False,
        "KLM_MODEL_APIS_ENABLED": False,
        "KLM_LOCAL_BOOTSTRAP_ENABLED": not production,
    }


def test_env_example_secret_remains_usable_locally_but_is_rejected_in_production():
    example_secret = dotenv_values(PROJECT_ROOT / ".env.example")["SECRET_KEY"]
    assert example_secret

    local_settings = Settings(**_settings_overrides(example_secret, production=False))
    assert local_settings.SECRET_KEY == example_secret

    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(**_settings_overrides(example_secret, production=True))


@pytest.mark.parametrize("secret_key", ["", "short-secret", "your-secret-key-here"])
def test_production_rejects_empty_short_or_known_jwt_secrets(secret_key):
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(**_settings_overrides(secret_key, production=True))


def test_production_accepts_a_distinct_long_jwt_secret():
    secret_key = "kLM_7vJmW5rD3xQ9pT2nH8cF6sA4zY1uB0eG"
    configured = Settings(**_settings_overrides(secret_key, production=True))
    assert configured.SECRET_KEY == secret_key


def test_production_rejects_app_and_blackbox_on_same_hostname():
    payload = _settings_overrides(
        "test-only-production-jwt-secret-with-32-bytes",
        production=True,
    )
    payload["BLACKBOX_FRONTEND_URL"] = "https://app.example.test/blackbox"

    with pytest.raises(ValidationError, match="origens HTTPS|hostnames distintos"):
        Settings(**payload)


def test_production_rejects_shared_parent_cookie_domain():
    payload = _settings_overrides(
        "test-only-production-jwt-secret-with-32-bytes",
        production=True,
    )
    payload["AUTH_REFRESH_COOKIE_DOMAIN"] = ".example.test"

    with pytest.raises(ValidationError, match="host-only"):
        Settings(**payload)


def test_production_requires_both_surfaces_in_cors():
    payload = _settings_overrides(
        "test-only-production-jwt-secret-with-32-bytes",
        production=True,
    )
    payload["CORS_ORIGINS"] = ["https://app.example.test"]

    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        Settings(**payload)


def test_model_apis_cannot_open_without_durable_usage_accounting():
    payload = _settings_overrides("local-test-secret", production=False)
    payload.update(
        {
            "KLM_RUNTIME_MODE": "staging",
            "KLM_ONLINE_APPROVED": True,
            "KLM_MODEL_APIS_ENABLED": True,
            "KLM_AI_USAGE_ACCOUNTING_ENABLED": False,
            "KLM_AI_BUDGET_BACKEND": "redis",
            "KLM_AI_TELEMETRY_BACKEND": "redis",
            "KLM_AI_CIRCUIT_BACKEND": "redis",
        }
    )

    with pytest.raises(ValidationError, match="USAGE_ACCOUNTING"):
        Settings(**payload)


def test_ai_gateway_event_retention_must_be_positive():
    payload = _settings_overrides("local-test-secret", production=False)
    payload["KLM_AI_GATEWAY_EVENTS_RETENTION_DAYS"] = 0

    with pytest.raises(ValidationError, match="Limites do runtime de IA"):
        Settings(**payload)


@pytest.mark.parametrize(
    "field",
    [
        "BANK_RECONCILIATION_UPLOAD_MAX_BYTES",
        "FINANCE_HARMONIZATION_UPLOAD_MAX_BYTES",
        "MIGRATION_UPLOAD_MAX_BYTES",
        "LEGAL_DOCX_UPLOAD_MAX_BYTES",
        "IMPORT_ARCHIVE_MAX_ENTRIES",
        "IMPORT_ARCHIVE_MAX_UNCOMPRESSED_BYTES",
        "IMPORT_ARCHIVE_MAX_COMPRESSION_RATIO",
    ],
)
def test_upload_and_archive_limits_must_be_positive(field):
    payload = _settings_overrides("local-test-secret", production=False)
    payload[field] = 0

    with pytest.raises(ValidationError, match="Limites de upload/importação"):
        Settings(**payload)


@pytest.mark.parametrize(
    "integration_secret_key",
    ["", "short-integration-key", "your-integration-secret-encryption-key"],
)
def test_production_rejects_missing_short_or_known_integration_keys(
    integration_secret_key,
):
    with pytest.raises(ValidationError, match="INTEGRATION_SECRET_ENCRYPTION_KEY"):
        Settings(
            **_settings_overrides(
                "test-only-production-jwt-secret-with-32-bytes",
                production=True,
                integration_secret_key=integration_secret_key,
            )
        )


def test_production_rejects_reusing_jwt_secret_for_integration_encryption():
    shared_secret = "test-only-shared-production-secret-with-32-bytes"

    with pytest.raises(ValidationError, match="INTEGRATION_SECRET_ENCRYPTION_KEY"):
        Settings(
            **_settings_overrides(
                shared_secret,
                production=True,
                integration_secret_key=shared_secret,
            )
        )
