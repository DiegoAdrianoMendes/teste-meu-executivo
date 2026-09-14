#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.oss.yml"
ENV_FILE="$ROOT/.local/oss.env"
VERIFY_SCRIPT="$ROOT/scripts/verify_oss_sidecars.py"
PROJECT_NAME="klm-oss-local"

PROFILE="all"
PROFILE_SET=false
CONFIG_ONLY=false
TEMP_ENV=""

usage() {
  cat <<'EOF'
Usage: scripts/start_oss_sidecars.sh [channels|collaboration|automation|crm|all] [--config-only]

  channels       Start Chatwoot and its private Postgres/Redis services.
  collaboration  Start Rocket.Chat and its Mongo replica set.
  automation     Start ChatterMate and its private Postgres/Redis services.
  crm            Start BottleCRM and its private Postgres/Redis services.
  all            Start all independent local profiles (default).
  --config-only  Generate/validate local config without pulling or starting images.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

cleanup() {
  if [[ -n "$TEMP_ENV" && -f "$TEMP_ENV" ]]; then
    rm -f "$TEMP_ENV"
  fi
}

for argument in "$@"; do
  case "$argument" in
    channels | collaboration | automation | crm | all)
      [[ "$PROFILE_SET" == false ]] || die "only one profile selector is allowed"
      PROFILE="$argument"
      PROFILE_SET=true
      ;;
    --config-only)
      CONFIG_ONLY=true
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown argument: $argument"
      ;;
  esac
done

require_command docker
require_command git
require_command python3
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"

[[ ! -L "$ROOT/.local" ]] || die ".local must be a real directory, not a symlink"
mkdir -p "$ROOT/.local"
chmod 700 "$ROOT/.local"
[[ ! -L "$ENV_FILE" ]] || die ".local/oss.env must not be a symlink"

git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  die "the OSS runtime must be generated inside its Git worktree"
}
git -C "$ROOT" check-ignore -q -- .local/oss.env || {
  die ".local/oss.env is not ignored by Git; refusing to generate secrets"
}

umask 077
TEMP_ENV="$(mktemp "$ROOT/.local/oss.env.XXXXXX")"
trap cleanup EXIT
python3 - "$ENV_FILE" "$TEMP_ENV" "$ROOT/.local/klm-hub.db" <<'PY'
from pathlib import Path
import base64
import re
import secrets
import sqlite3
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
database = Path(sys.argv[3])

existing = {}
if source.is_file():
    for line_number, raw_line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SystemExit(f"invalid dotenv line {line_number} in {source}")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in existing:
            raise SystemExit(f"invalid or duplicate dotenv key on line {line_number}: {key}")
        existing[key] = value

workspace_id = existing.get("KLM_OSS_KLM_WORKSPACE_ID", "")
if database.is_file():
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        row = connection.execute(
            "SELECT id FROM workspaces WHERE slug = ? AND deleted_at IS NULL LIMIT 1",
            ("klm-hub-local",),
        ).fetchone()
        connection.close()
        if row and row[0]:
            workspace_id = str(row[0])
    except sqlite3.Error:
        pass

def token(bytes_count=32):
    return secrets.token_hex(bytes_count)

def password():
    return "Klm!" + secrets.token_urlsafe(24)

def fernet_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")

defaults = {
    "KLM_OSS_CHATWOOT_PORT": "3100",
    "KLM_OSS_ROCKETCHAT_PORT": "3200",
    "KLM_OSS_CHATTERMATE_PORT": "3300",
    "KLM_OSS_BOTTLECRM_PORT": "3400",
    "KLM_OSS_START_TIMEOUT": "1200",
    "KLM_OSS_TIMEZONE": "America/Sao_Paulo",
    "KLM_OSS_KLM_WORKSPACE_ID": workspace_id,
    "KLM_OSS_LAUNCH_SECRET": token(),
    "KLM_OSS_CHATWOOT_POSTGRES_PASSWORD": token(),
    "KLM_OSS_CHATWOOT_REDIS_PASSWORD": token(),
    "KLM_OSS_CHATWOOT_SECRET_KEY_BASE": token(64),
    "KLM_OSS_CHATWOOT_ACTIVE_RECORD_ENCRYPTION_PRIMARY_KEY": token(),
    "KLM_OSS_CHATWOOT_ACTIVE_RECORD_ENCRYPTION_DETERMINISTIC_KEY": token(),
    "KLM_OSS_CHATWOOT_ACTIVE_RECORD_ENCRYPTION_KEY_DERIVATION_SALT": token(),
    "KLM_OSS_CHATWOOT_ACCOUNT_ID": "100001",
    "KLM_OSS_CHATWOOT_USER_ID": "100001",
    "KLM_OSS_CHATWOOT_INBOX_ID": "100001",
    "KLM_OSS_CHATWOOT_ADMIN_NAME": "Kleist Monteiro",
    "KLM_OSS_CHATWOOT_ADMIN_EMAIL": "admin@local.klmadvogados.com.br",
    "KLM_OSS_CHATWOOT_ADMIN_PASSWORD": password(),
    "KLM_OSS_CHATWOOT_API_ACCESS_TOKEN": token(),
    "KLM_OSS_CHATWOOT_WEBHOOK_SECRET": token(),
    "KLM_OSS_ROCKETCHAT_ADMIN_NAME": "Kleist Monteiro",
    "KLM_OSS_ROCKETCHAT_ADMIN_USERNAME": "klm-admin",
    "KLM_OSS_ROCKETCHAT_ADMIN_EMAIL": "admin@local.klmadvogados.com.br",
    "KLM_OSS_ROCKETCHAT_ADMIN_PASSWORD": password(),
    "KLM_OSS_ROCKETCHAT_WEBHOOK_TOKEN": token(),
    "KLM_OSS_CHATTERMATE_POSTGRES_PASSWORD": token(),
    "KLM_OSS_CHATTERMATE_REDIS_PASSWORD": token(),
    "KLM_OSS_CHATTERMATE_CONVERSATION_SECRET": token(48),
    "KLM_OSS_CHATTERMATE_ENCRYPTION_KEY": fernet_key(),
    "KLM_OSS_CHATTERMATE_JWT_SECRET": token(48),
    "KLM_OSS_CHATTERMATE_ADMIN_NAME": "Kleist Monteiro",
    "KLM_OSS_CHATTERMATE_ADMIN_EMAIL": "admin@local.klmadvogados.com.br",
    "KLM_OSS_CHATTERMATE_ADMIN_PASSWORD": password(),
    "KLM_OSS_BOTTLECRM_POSTGRES_PASSWORD": token(),
    "KLM_OSS_BOTTLECRM_POSTGRES_SUPERUSER_PASSWORD": token(),
    "KLM_OSS_BOTTLECRM_REDIS_PASSWORD": token(),
    "KLM_OSS_BOTTLECRM_SECRET_KEY": token(64),
    "KLM_OSS_BOTTLECRM_BRIDGE_SECRET": token(48),
    "KLM_OSS_BOTTLECRM_ADMIN_NAME": "Kleist Monteiro",
    "KLM_OSS_BOTTLECRM_ADMIN_EMAIL": "admin@local.klmadvogados.com.br",
    "KLM_OSS_BOTTLECRM_ADMIN_PASSWORD": password(),
}
values = {key: existing.get(key, value) for key, value in defaults.items()}
if workspace_id:
    values["KLM_OSS_KLM_WORKSPACE_ID"] = workspace_id

lines = [
    "# Generated and reconciled by scripts/start_oss_sidecars.sh.",
    "# Local sidecar credentials only. Do not commit or reuse elsewhere.",
    "",
]
lines.extend(f"{key}={value}" for key, value in values.items())
target.write_text("\n".join(lines) + "\n", encoding="ascii")
target.chmod(0o600)
PY
mv "$TEMP_ENV" "$ENV_FILE"
TEMP_ENV=""
trap - EXIT
chmod 600 "$ENV_FILE"
printf 'Reconciled %s with mode 0600.\n' "$ENV_FILE"

COMPOSE=(
  docker compose
  --project-name "$PROJECT_NAME"
  --env-file "$ENV_FILE"
  --file "$COMPOSE_FILE"
)

case "$PROFILE" in
  channels)
    COMPOSE+=(--profile channels)
    ;;
  collaboration)
    COMPOSE+=(--profile collaboration)
    ;;
  automation)
    COMPOSE+=(--profile automation)
    ;;
  crm)
    COMPOSE+=(--profile crm)
    ;;
  all)
    COMPOSE+=(--profile channels --profile collaboration --profile automation --profile crm)
    ;;
esac

if [[ "$CONFIG_ONLY" == true ]]; then
  python3 "$VERIFY_SCRIPT" --env-file "$ENV_FILE" --profile "$PROFILE"
  "${COMPOSE[@]}" config --quiet
  printf 'OSS sidecar configuration is valid; no images were pulled or started.\n'
  exit 0
fi

docker info >/dev/null 2>&1 || die "Docker daemon is not available"
python3 "$VERIFY_SCRIPT" --env-file "$ENV_FILE" --profile "$PROFILE" --preflight

printf 'Starting KLM OSS profile: %s\n' "$PROFILE"
if ! "${COMPOSE[@]}" up --detach; then
  "${COMPOSE[@]}" ps --all || true
  die "Docker Compose failed to start the selected profile"
fi

if ! python3 "$VERIFY_SCRIPT" --env-file "$ENV_FILE" --profile "$PROFILE" --runtime; then
  "${COMPOSE[@]}" ps --all || true
  "${COMPOSE[@]}" logs --tail 100 || true
  die "selected OSS sidecars did not become healthy"
fi

python3 - "$ENV_FILE" "$PROFILE" <<'PY'
from pathlib import Path
import sys

values = {}
for raw_line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key] = value

profile = sys.argv[2]
print("KLM OSS sidecars are healthy:")
if profile in {"channels", "all"}:
    print(f"  Chatwoot:    http://127.0.0.1:{values['KLM_OSS_CHATWOOT_PORT']}")
if profile in {"collaboration", "all"}:
    print(f"  Rocket.Chat: http://127.0.0.1:{values['KLM_OSS_ROCKETCHAT_PORT']}")
if profile in {"automation", "all"}:
    print(f"  ChatterMate: http://127.0.0.1:{values['KLM_OSS_CHATTERMATE_PORT']}")
if profile in {"crm", "all"}:
    print(f"  BottleCRM:   http://127.0.0.1:{values['KLM_OSS_BOTTLECRM_PORT']}")
PY
