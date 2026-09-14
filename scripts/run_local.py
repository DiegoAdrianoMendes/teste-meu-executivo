"""Inicia o KLM Hub com SQLite e todos os efeitos externos bloqueados."""

from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
from pathlib import Path

LOCAL_INTEGRATION_SECRET_KEY_FILE = "integration-secret-encryption.key"
_MIN_LOCAL_INTEGRATION_SECRET_KEY_BYTES = 32

FORCED_LOCAL_ENV = {
    "APP_ENV": "development",
    "ENVIRONMENT": "development",
    "KLM_RUNTIME_MODE": "local_simulation",
    "KLM_EXTERNAL_ACTIONS_ENABLED": "false",
    "KLM_PRIVATE_AWS_STORAGE_ENABLED": "false",
    "KLM_AWS_ENABLED": "false",
    "KLM_ONLINE_APPROVED": "false",
    "KLM_MODEL_APIS_ENABLED": "false",
    "KLM_LOCAL_BOOTSTRAP_ENABLED": "true",
    "AI_PROVIDER": "fake",
    "KLM_LOCAL_MODELS_ENABLED": "false",
    "KLM_AI_LEGACY_DIRECT_CALLS_ENABLED": "false",
}
EXTERNAL_CREDENTIAL_ENV = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "LANGCHAIN_API_KEY",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "SAAS_ASAAS_ACCESS_TOKEN",
    "SAAS_ASAAS_WEBHOOK_TOKEN",
    "MERCADO_PAGO_ACCESS_TOKEN",
    "MERCADO_PAGO_WEBHOOK_SECRET",
    "META_ADS_ACCESS_TOKEN",
    "META_ADS_APP_SECRET",
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
    "OPENCLAW_WEBHOOK_SECRET",
    "SLACK_BOT_TOKEN",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_VERIFY_TOKEN",
    "WHATSAPP_APP_SECRET",
    "WHATSAPP_QR_GATEWAY_API_KEY",
    "META_APP_SECRET",
    "ZAPSIGN_API_TOKEN",
    "ZAPSIGN_WEBHOOK_SECRET",
    "RESEND_API_KEY",
)

OSS_ENV_MAP = {
    "KLM_OSS_KLM_WORKSPACE_ID": "KLM_OSS_WORKSPACE_ID",
    "KLM_OSS_LAUNCH_SECRET": "KLM_OSS_LAUNCH_SECRET",
    "KLM_OSS_CHATWOOT_ACCOUNT_ID": "KLM_CHATWOOT_ACCOUNT_ID",
    "KLM_OSS_CHATWOOT_INBOX_ID": "KLM_CHATWOOT_INBOX_ID",
    "KLM_OSS_CHATWOOT_API_ACCESS_TOKEN": "KLM_CHATWOOT_API_ACCESS_TOKEN",
    "KLM_OSS_CHATWOOT_ADMIN_EMAIL": "KLM_CHATWOOT_ADMIN_EMAIL",
    "KLM_OSS_CHATWOOT_ADMIN_PASSWORD": "KLM_CHATWOOT_ADMIN_PASSWORD",
    "KLM_OSS_CHATWOOT_WEBHOOK_SECRET": "KLM_CHATWOOT_WEBHOOK_SECRET",
    "KLM_OSS_ROCKETCHAT_ADMIN_USERNAME": "KLM_ROCKETCHAT_ADMIN_USERNAME",
    "KLM_OSS_ROCKETCHAT_ADMIN_EMAIL": "KLM_ROCKETCHAT_ADMIN_EMAIL",
    "KLM_OSS_ROCKETCHAT_ADMIN_PASSWORD": "KLM_ROCKETCHAT_ADMIN_PASSWORD",
    "KLM_OSS_ROCKETCHAT_WEBHOOK_TOKEN": "KLM_ROCKETCHAT_WEBHOOK_TOKEN",
    "KLM_OSS_CHATTERMATE_ADMIN_EMAIL": "KLM_CHATTERMATE_ADMIN_EMAIL",
    "KLM_OSS_CHATTERMATE_ADMIN_PASSWORD": "KLM_CHATTERMATE_ADMIN_PASSWORD",
    "KLM_OSS_BOTTLECRM_ADMIN_EMAIL": "KLM_BOTTLECRM_ADMIN_EMAIL",
    "KLM_OSS_BOTTLECRM_ADMIN_PASSWORD": "KLM_BOTTLECRM_ADMIN_PASSWORD",
    "KLM_OSS_BOTTLECRM_BRIDGE_SECRET": "KLM_BOTTLECRM_BRIDGE_SECRET",
}


def _read_local_oss_environment(root: Path) -> dict[str, str]:
    """Read only the local sidecar allowlist from a private, regular dotenv file."""
    path = root / ".local" / "oss.env"
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{path} deve ser um arquivo regular, não um link.")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError(f"{path} deve ter permissão 0600.")

    parsed: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"Linha {line_number} inválida em {path}.")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key) is None or key in parsed:
            raise RuntimeError(f"Chave inválida ou repetida na linha {line_number} de {path}.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if "\x00" in value or "\n" in value or "\r" in value:
            raise RuntimeError(f"Valor inválido na linha {line_number} de {path}.")
        parsed[key] = value

    local_values = {
        target: parsed[source] for source, target in OSS_ENV_MAP.items() if parsed.get(source)
    }
    ports = {
        "KLM_CHATWOOT_URL": ("KLM_OSS_CHATWOOT_PORT", "3100"),
        "KLM_ROCKETCHAT_URL": ("KLM_OSS_ROCKETCHAT_PORT", "3200"),
        "KLM_CHATTERMATE_URL": ("KLM_OSS_CHATTERMATE_PORT", "3300"),
        "KLM_BOTTLECRM_URL": ("KLM_OSS_BOTTLECRM_PORT", "3400"),
    }
    for target, (source, default) in ports.items():
        raw_port = parsed.get(source, default)
        if not raw_port.isdigit() or not 1024 <= int(raw_port) <= 65535:
            raise RuntimeError(f"{source} inválida em {path}.")
        local_values[target] = f"http://127.0.0.1:{raw_port}"
    local_values["KLM_OSS_LOCAL_BRIDGES_ENABLED"] = "true"
    return local_values


def _read_private_integration_secret(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"{path} deve ser um arquivo regular, não um link.")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RuntimeError(f"{path} deve ter permissão 0600.")

    raw_value = path.read_text(encoding="utf-8")
    value = raw_value.removesuffix("\n")
    if (
        "\n" in value
        or "\r" in value
        or len(value.encode("utf-8")) < _MIN_LOCAL_INTEGRATION_SECRET_KEY_BYTES
    ):
        raise RuntimeError(f"{path} não contém uma chave de criptografia forte.")
    return value


def _local_integration_secret(root: Path) -> str:
    path = root / ".local" / LOCAL_INTEGRATION_SECRET_KEY_FILE
    if path.exists() or path.is_symlink():
        return _read_private_integration_secret(path)

    value = secrets.token_urlsafe(48)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return _read_private_integration_secret(path)

    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as key_file:
            key_file.write(f"{value}\n")
            key_file.flush()
            os.fsync(key_file.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return value


def configure_local_environment(
    *,
    root: Path,
    host: str,
    port: int,
    database_path: Path | None = None,
) -> None:
    local_dir = root / ".local"
    local_dir.mkdir(parents=True, exist_ok=True)
    local_database = (database_path or (local_dir / "klm-hub.db")).resolve()
    local_database.parent.mkdir(parents=True, exist_ok=True)

    # The local launcher must never inherit a shell-level staging/production DB.
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{local_database}"
    os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{local_database}"
    os.environ["INTEGRATION_SECRET_ENCRYPTION_KEY"] = _local_integration_secret(root)
    os.environ.update(FORCED_LOCAL_ENV)
    for variable in EXTERNAL_CREDENTIAL_ENV:
        os.environ[variable] = ""
    os.environ.update(_read_local_oss_environment(root))
    os.environ.setdefault("PAPERCLIP_PUBLIC_URL", f"http://{host}:{port}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Executa o backend local do KLM Hub.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8010, type=int)
    parser.add_argument(
        "--database-path",
        type=Path,
        help="SQLite local isolado; o padrão preserva .local/klm-hub.db.",
    )
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    configure_local_environment(
        root=root,
        host=args.host,
        port=args.port,
        database_path=args.database_path,
    )

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        app_dir=str(root),
    )


if __name__ == "__main__":
    main()
