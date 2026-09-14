"""Matriz fail-closed do runtime local do KLM Hub."""

import os
import stat

import pytest

from app.core.config import Settings, settings
from app.core.klm_runtime import (
    automation_worker_allowed,
    aws_external_access_allowed,
    aws_private_storage_allowed,
    external_delivery_allowed,
    get_runtime_status,
    local_bootstrap_allowed,
    local_fake_model_execution_allowed,
    model_api_execution_allowed,
)
from scripts.run_local import (
    EXTERNAL_CREDENTIAL_ENV,
    LOCAL_INTEGRATION_SECRET_KEY_FILE,
    configure_local_environment,
)


@pytest.mark.parametrize(
    ("runtime_mode", "online_approved", "external_enabled", "expected"),
    [
        ("local_simulation", False, False, False),
        ("local_simulation", True, False, False),
        ("local_simulation", False, True, False),
        ("local_simulation", True, True, False),
        ("production", False, False, False),
        ("production", True, False, False),
        ("production", False, True, False),
        ("production", True, True, True),
    ],
)
def test_external_delivery_requires_every_gate(
    monkeypatch, runtime_mode, online_approved, external_enabled, expected
):
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", runtime_mode)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", online_approved)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", external_enabled)
    assert external_delivery_allowed() is expected


@pytest.mark.parametrize(
    ("runtime_mode", "online_approved", "external_enabled", "aws_enabled", "expected"),
    [
        ("local_simulation", True, True, True, False),
        ("production", False, True, True, False),
        ("production", True, False, True, False),
        ("production", True, True, False, False),
        ("production", True, True, True, True),
    ],
)
def test_aws_access_requires_the_complete_external_gate(
    monkeypatch,
    runtime_mode,
    online_approved,
    external_enabled,
    aws_enabled,
    expected,
):
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", runtime_mode)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", online_approved)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", external_enabled)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", aws_enabled)

    assert aws_external_access_allowed() is expected


@pytest.mark.parametrize(
    (
        "runtime_mode",
        "private_storage_enabled",
        "kms_key_id",
        "online_approved",
        "external_enabled",
        "expected",
    ),
    [
        ("local_simulation", True, "alias/private", False, False, False),
        ("production", False, "alias/private", False, False, False),
        ("production", True, "", False, False, False),
        ("production", True, "alias/private", False, False, True),
        ("production", True, "alias/private", True, False, True),
        ("production", True, "alias/private", False, True, True),
    ],
)
def test_private_aws_storage_has_a_dedicated_fail_closed_gate(
    monkeypatch,
    runtime_mode,
    private_storage_enabled,
    kms_key_id,
    online_approved,
    external_enabled,
    expected,
):
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", runtime_mode)
    monkeypatch.setattr(
        settings, "KLM_PRIVATE_AWS_STORAGE_ENABLED", private_storage_enabled
    )
    monkeypatch.setattr(settings, "S3_KMS_KEY_ID", kms_key_id)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", online_approved)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", external_enabled)

    assert aws_private_storage_allowed() is expected


def test_settings_allow_private_aws_storage_with_all_provider_gates_closed():
    runtime_settings = Settings(
        _env_file=None,
        KLM_RUNTIME_MODE="production",
        KLM_PRIVATE_AWS_STORAGE_ENABLED=True,
        KLM_AWS_ENABLED=False,
        KLM_ONLINE_APPROVED=False,
        KLM_EXTERNAL_ACTIONS_ENABLED=False,
        S3_KMS_KEY_ID="alias/meu-executivo-private",
    )

    assert runtime_settings.KLM_PRIVATE_AWS_STORAGE_ENABLED is True
    assert runtime_settings.KLM_AWS_ENABLED is False
    assert runtime_settings.is_local_only is False


def test_settings_reject_private_aws_storage_without_kms():
    with pytest.raises(ValueError, match="S3_KMS_KEY_ID"):
        Settings(
            _env_file=None,
            KLM_RUNTIME_MODE="production",
            KLM_PRIVATE_AWS_STORAGE_ENABLED=True,
            KLM_AWS_ENABLED=False,
            KLM_ONLINE_APPROVED=False,
            KLM_EXTERNAL_ACTIONS_ENABLED=False,
            S3_KMS_KEY_ID="",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "KLM_RUNTIME_MODE": "local_simulation",
            "KLM_ONLINE_APPROVED": True,
            "KLM_EXTERNAL_ACTIONS_ENABLED": True,
            "KLM_AWS_ENABLED": False,
        },
        {
            "KLM_RUNTIME_MODE": "local_simulation",
            "KLM_ONLINE_APPROVED": True,
            "KLM_EXTERNAL_ACTIONS_ENABLED": True,
            "KLM_AWS_ENABLED": True,
        },
    ],
)
def test_settings_reject_external_or_aws_gates_in_local_simulation(overrides):
    with pytest.raises(ValueError, match="local_simulation"):
        Settings(_env_file=None, **overrides)


def test_settings_reject_private_aws_storage_in_local_simulation():
    with pytest.raises(ValueError, match="local_simulation"):
        Settings(
            _env_file=None,
            KLM_RUNTIME_MODE="local_simulation",
            KLM_PRIVATE_AWS_STORAGE_ENABLED=True,
            S3_KMS_KEY_ID="alias/meu-executivo-private",
        )


def test_settings_reject_aws_without_external_approval():
    with pytest.raises(ValueError, match="KLM_EXTERNAL_ACTIONS_ENABLED"):
        Settings(
            _env_file=None,
            KLM_RUNTIME_MODE="production",
            KLM_ONLINE_APPROVED=True,
            KLM_EXTERNAL_ACTIONS_ENABLED=False,
            KLM_AWS_ENABLED=True,
        )


def test_settings_allow_explicit_model_api_in_local_simulation():
    runtime_settings = Settings(
        _env_file=None,
        KLM_RUNTIME_MODE="local_simulation",
        KLM_ONLINE_APPROVED=True,
        KLM_MODEL_APIS_ENABLED=True,
        KLM_EXTERNAL_ACTIONS_ENABLED=False,
        KLM_AWS_ENABLED=False,
        KLM_AI_BUDGET_BACKEND="redis",
        KLM_AI_TELEMETRY_BACKEND="redis",
        KLM_AI_CIRCUIT_BACKEND="redis",
    )

    assert runtime_settings.uses_local_infrastructure is True
    assert runtime_settings.KLM_MODEL_APIS_ENABLED is True
    assert runtime_settings.KLM_AI_BUDGET_BACKEND == "redis"
    assert runtime_settings.KLM_AI_TELEMETRY_BACKEND == "redis"
    assert runtime_settings.KLM_AI_CIRCUIT_BACKEND == "redis"


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("192.168.1.50", False),
        ("10.0.0.8", False),
        ("localhost", True),
        ("testclient", True),
        ("8.8.8.8", False),
        ("example.com", False),
    ],
)
def test_local_bootstrap_accepts_only_loopback_hosts(monkeypatch, host, expected):
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_LOCAL_BOOTSTRAP_ENABLED", True)
    assert local_bootstrap_allowed(host) is expected


@pytest.mark.parametrize(
    ("environment", "bootstrap_enabled", "external_enabled"),
    [
        ("production", True, False),
        ("development", False, False),
        ("development", True, True),
    ],
)
def test_local_bootstrap_fails_closed_when_runtime_contract_changes(
    monkeypatch, environment, bootstrap_enabled, external_enabled
):
    monkeypatch.setattr(settings, "APP_ENV", environment)
    monkeypatch.setattr(settings, "KLM_LOCAL_BOOTSTRAP_ENABLED", bootstrap_enabled)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", external_enabled)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    assert local_bootstrap_allowed("127.0.0.1") is False


def test_runtime_status_keeps_every_external_provider_offline(monkeypatch):
    monkeypatch.setattr(settings, "AI_PROVIDER", "fake")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)
    payload = get_runtime_status()
    assert payload["localOnly"] is True
    assert payload["onlineApproved"] is False
    assert payload["providers"]["models"] == "local_fake"
    assert {
        state for provider, state in payload["providers"].items() if provider != "models"
    } == {"offline"}


def test_model_api_does_not_switch_local_infrastructure(monkeypatch):
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_MODEL_APIS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)

    payload = get_runtime_status()

    assert payload["localOnly"] is False
    assert payload["localInfrastructure"] is True
    assert payload["modelApisEnabled"] is True
    assert payload["providers"]["models"] == "online_approved"
    assert payload["providers"]["aws"] == "offline"


def test_model_reasoning_and_automation_are_independent_from_channel_delivery(
    monkeypatch,
):
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_MODEL_APIS_ENABLED", True)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", True)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)

    assert model_api_execution_allowed() is True
    assert automation_worker_allowed() is True
    assert external_delivery_allowed() is False


def test_automation_stays_closed_when_neither_models_nor_channels_are_approved(
    monkeypatch,
):
    monkeypatch.setattr(settings, "AI_PROVIDER", "api_gateway")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_MODEL_APIS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)

    assert model_api_execution_allowed() is False
    assert automation_worker_allowed() is False


def test_local_fake_enables_internal_reasoning_without_opening_external_effects(
    monkeypatch,
):
    monkeypatch.setattr(settings, "AI_PROVIDER", "fake")
    monkeypatch.setattr(settings, "KLM_RUNTIME_MODE", "local_simulation")
    monkeypatch.setattr(settings, "KLM_MODEL_APIS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_ONLINE_APPROVED", False)
    monkeypatch.setattr(settings, "KLM_EXTERNAL_ACTIONS_ENABLED", False)
    monkeypatch.setattr(settings, "KLM_AWS_ENABLED", False)

    payload = get_runtime_status()

    assert local_fake_model_execution_allowed() is True
    assert model_api_execution_allowed() is True
    assert automation_worker_allowed() is True
    assert external_delivery_allowed() is False
    assert payload["modelApisEnabled"] is False
    assert payload["providers"]["models"] == "local_fake"


def test_local_launcher_overrides_inherited_online_gates_and_model_keys(
    monkeypatch,
    tmp_path,
):
    inherited = {
        "DATABASE_URL": "postgresql+asyncpg://prod:secret@db.example/prod",
        "DATABASE_URL_SYNC": "postgresql://prod:secret@db.example/prod",
        "APP_ENV": "production",
        "ENVIRONMENT": "production",
        "KLM_RUNTIME_MODE": "production",
        "KLM_EXTERNAL_ACTIONS_ENABLED": "true",
        "KLM_PRIVATE_AWS_STORAGE_ENABLED": "true",
        "KLM_AWS_ENABLED": "true",
        "KLM_ONLINE_APPROVED": "true",
        "KLM_MODEL_APIS_ENABLED": "true",
        "KLM_LOCAL_BOOTSTRAP_ENABLED": "false",
        "AI_PROVIDER": "ollama",
        "KLM_LOCAL_MODELS_ENABLED": "true",
        "KLM_AI_LEGACY_DIRECT_CALLS_ENABLED": "true",
        "OPENAI_API_KEY": "real-openai-key",
        "ANTHROPIC_API_KEY": "real-anthropic-key",
        "GEMINI_API_KEY": "real-gemini-key",
        "AWS_ACCESS_KEY_ID": "real-aws-key",
        "AWS_SECRET_ACCESS_KEY": "real-aws-secret",
        "WHATSAPP_ACCESS_TOKEN": "real-whatsapp-token",
        "WHATSAPP_VERIFY_TOKEN": "real-verify-token",
        "WHATSAPP_APP_SECRET": "real-app-secret",
        "ZAPSIGN_API_TOKEN": "real-zapsign-token",
        "GOOGLE_ADS_REFRESH_TOKEN": "real-google-refresh-token",
        "META_ADS_ACCESS_TOKEN": "real-meta-token",
        "SLACK_BOT_TOKEN": "real-slack-token",
        "RESEND_API_KEY": "real-resend-key",
        "INTEGRATION_SECRET_ENCRYPTION_KEY": "inherited-integration-secret-key",
    }
    for key, value in inherited.items():
        monkeypatch.setenv(key, value)

    configure_local_environment(root=tmp_path, host="127.0.0.1", port=8010)
    runtime_settings = Settings(_env_file=None)

    assert os.environ["APP_ENV"] == "development"
    expected_database = (tmp_path / ".local" / "klm-hub.db").resolve()
    assert os.environ["DATABASE_URL"] == f"sqlite+aiosqlite:///{expected_database}"
    assert os.environ["DATABASE_URL_SYNC"] == f"sqlite:///{expected_database}"
    assert os.environ["ENVIRONMENT"] == "development"
    assert os.environ["KLM_RUNTIME_MODE"] == "local_simulation"
    assert os.environ["KLM_EXTERNAL_ACTIONS_ENABLED"] == "false"
    assert os.environ["KLM_PRIVATE_AWS_STORAGE_ENABLED"] == "false"
    assert os.environ["KLM_AWS_ENABLED"] == "false"
    assert os.environ["KLM_ONLINE_APPROVED"] == "false"
    assert os.environ["KLM_MODEL_APIS_ENABLED"] == "false"
    assert os.environ["KLM_LOCAL_BOOTSTRAP_ENABLED"] == "true"
    assert os.environ["AI_PROVIDER"] == "fake"
    assert os.environ["KLM_LOCAL_MODELS_ENABLED"] == "false"
    assert os.environ["KLM_AI_LEGACY_DIRECT_CALLS_ENABLED"] == "false"
    assert os.environ["OPENAI_API_KEY"] == ""
    assert os.environ["ANTHROPIC_API_KEY"] == ""
    assert os.environ["GEMINI_API_KEY"] == ""
    assert os.environ["AWS_ACCESS_KEY_ID"] == ""
    assert os.environ["AWS_SECRET_ACCESS_KEY"] == ""
    assert os.environ["WHATSAPP_ACCESS_TOKEN"] == ""
    assert os.environ["WHATSAPP_VERIFY_TOKEN"] == ""
    assert os.environ["WHATSAPP_APP_SECRET"] == ""
    assert os.environ["ZAPSIGN_API_TOKEN"] == ""
    assert os.environ["GOOGLE_ADS_REFRESH_TOKEN"] == ""
    assert os.environ["META_ADS_ACCESS_TOKEN"] == ""
    assert os.environ["SLACK_BOT_TOKEN"] == ""
    assert os.environ["RESEND_API_KEY"] == ""
    assert runtime_settings.is_local_only is True
    assert runtime_settings.uses_local_infrastructure is True
    assert runtime_settings.AI_PROVIDER == "fake"


def test_local_launcher_accepts_an_isolated_sqlite_database(monkeypatch, tmp_path):
    isolated_database = tmp_path / "isolated" / "e2e.db"

    configure_local_environment(
        root=tmp_path,
        host="127.0.0.1",
        port=8110,
        database_path=isolated_database,
    )

    assert isolated_database.parent.is_dir()
    assert os.environ["DATABASE_URL"] == (
        f"sqlite+aiosqlite:///{isolated_database.resolve()}"
    )
    assert os.environ["DATABASE_URL_SYNC"] == (
        f"sqlite:///{isolated_database.resolve()}"
    )


def test_local_launcher_persists_private_dedicated_integration_key(
    monkeypatch,
    tmp_path,
):
    inherited_key = "inherited-integration-secret-key-with-more-than-32-bytes"
    monkeypatch.setenv("INTEGRATION_SECRET_ENCRYPTION_KEY", inherited_key)
    monkeypatch.setenv("SECRET_KEY", "public-local-jwt-secret-with-more-than-32-bytes")

    configure_local_environment(root=tmp_path, host="127.0.0.1", port=8010)

    key_path = tmp_path / ".local" / LOCAL_INTEGRATION_SECRET_KEY_FILE
    generated_key = key_path.read_text(encoding="utf-8").strip()
    assert os.environ["INTEGRATION_SECRET_ENCRYPTION_KEY"] == generated_key
    assert generated_key != inherited_key
    assert generated_key != os.environ["SECRET_KEY"]
    assert len(generated_key.encode("utf-8")) >= 32
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    configure_local_environment(root=tmp_path, host="127.0.0.1", port=8010)

    assert os.environ["INTEGRATION_SECRET_ENCRYPTION_KEY"] == generated_key
    assert key_path.read_text(encoding="utf-8").strip() == generated_key


def test_local_launcher_rejects_world_readable_integration_key(tmp_path):
    local_dir = tmp_path / ".local"
    local_dir.mkdir()
    key_path = local_dir / LOCAL_INTEGRATION_SECRET_KEY_FILE
    key_path.write_text(
        "existing-integration-secret-key-with-more-than-32-bytes\n",
        encoding="utf-8",
    )
    key_path.chmod(0o644)

    with pytest.raises(RuntimeError, match="0600"):
        configure_local_environment(root=tmp_path, host="127.0.0.1", port=8010)


def test_local_launcher_clears_every_registered_external_credential(
    monkeypatch,
    tmp_path,
):
    for variable in EXTERNAL_CREDENTIAL_ENV:
        monkeypatch.setenv(variable, f"secret-{variable.lower()}")

    configure_local_environment(root=tmp_path, host="127.0.0.1", port=8010)

    assert EXTERNAL_CREDENTIAL_ENV
    assert all(os.environ[variable] == "" for variable in EXTERNAL_CREDENTIAL_ENV)
