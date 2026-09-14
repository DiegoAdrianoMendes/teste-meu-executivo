"""S21: APP_ENV normalizado; postura de produção não escapa por grafia."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, fold_app_env, normalize_app_env


@pytest.mark.parametrize(
    ("bruto", "esperado"),
    [
        ("Production", "production"),
        (" PROD ", "production"),
        ("development", "development"),
        ("local", "development"),
        ("Staging", "staging"),
        ("test", "test"),
    ],
)
def test_normalize_app_env(bruto, esperado):
    assert normalize_app_env(bruto) == esperado


def test_unknown_app_env_fails_loudly():
    with pytest.raises(ValueError, match="APP_ENV inválido"):
        normalize_app_env("qualquer")


def test_fold_promotes_known_deploy_label_but_ignores_free_label():
    assert fold_app_env("development", "Production") == "production"
    assert fold_app_env("development", "blue") == "development"
    assert fold_app_env("production", "development") == "production"


def test_settings_accept_local_and_reject_unknown():
    assert Settings(_env_file=None, APP_ENV="local").APP_ENV == "development"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, APP_ENV="qualquer")
