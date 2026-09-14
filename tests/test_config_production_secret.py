from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_INTEGRATION_SECRET_KEY = "test-only-integration-key-7Hf9pQ2wX4mK8cR6vN3z"


def _example_secret_key() -> str:
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("SECRET_KEY="):
            return line.partition("=")[2]
    raise AssertionError("SECRET_KEY não encontrada no .env.example")


def _settings(*, app_env: str, secret_key: str) -> Settings:
    return Settings(
        _env_file=None,
        APP_ENV=app_env,
        ENVIRONMENT="",
        SECRET_KEY=secret_key,
        INTEGRATION_SECRET_ENCRYPTION_KEY=TEST_INTEGRATION_SECRET_KEY,
        KLM_RUNTIME_MODE="local_simulation",
        KLM_EXTERNAL_ACTIONS_ENABLED=False,
        KLM_AWS_ENABLED=False,
        KLM_ONLINE_APPROVED=False,
        KLM_MODEL_APIS_ENABLED=False,
        KLM_LOCAL_BOOTSTRAP_ENABLED=app_env != "production",
        AUTH_REFRESH_COOKIE_SAMESITE="lax",
        FRONTEND_URL="https://app.example.test",
        BLACKBOX_FRONTEND_URL="https://blackbox.example.test",
        CORS_ORIGINS=[
            "https://app.example.test",
            "https://blackbox.example.test",
        ],
        S3_PRIVATE_BUCKET_REQUIRED=True,
    )


def test_env_example_secret_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        _settings(app_env="production", secret_key=_example_secret_key())


@pytest.mark.parametrize("secret_key", ["", "short-production-secret"])
def test_empty_or_short_secret_is_rejected_in_production(secret_key: str) -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        _settings(app_env="production", secret_key=secret_key)


def test_env_example_secret_remains_usable_for_local_development() -> None:
    local_settings = _settings(app_env="development", secret_key=_example_secret_key())

    assert local_settings.SECRET_KEY == _example_secret_key()


def test_random_length_secret_is_accepted_in_production() -> None:
    production_settings = _settings(
        app_env="production",
        secret_key="test-only-production-secret-with-more-than-32-bytes",
    )

    assert production_settings.is_production is True
