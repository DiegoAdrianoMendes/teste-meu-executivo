#!/bin/zsh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -z "${KLM_MCP_GRANT_FILE:-}" || -z "${KLM_MCP_KEY_FILE:-}" ]]; then
  printf 'KLM_MCP_GRANT_FILE e KLM_MCP_KEY_FILE são obrigatórios.\n' >&2
  exit 2
fi

export DATABASE_URL="${KLM_MCP_DATABASE_URL:-sqlite+aiosqlite:///$ROOT/.local/klm-hub.db}"
export KLM_RUNTIME_MODE="local_simulation"
export KLM_EXTERNAL_ACTIONS_ENABLED="false"
export KLM_AWS_ENABLED="false"
export KLM_ONLINE_APPROVED="false"

cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m app.mcp
