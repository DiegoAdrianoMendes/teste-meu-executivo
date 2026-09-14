#!/bin/zsh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_PORT="${KLM_BACKEND_PORT:-8010}"
FRONTEND_PORT="${KLM_FRONTEND_PORT:-4173}"
BACKEND_SESSION="${KLM_BACKEND_SCREEN_SESSION:-klm-hub-backend-${BACKEND_PORT}}"
FRONTEND_SESSION="${KLM_FRONTEND_SCREEN_SESSION:-klm-hub-frontend-${FRONTEND_PORT}}"
WAIT_ATTEMPTS="${KLM_WAIT_ATTEMPTS:-40}"
WAIT_SLEEP_SECONDS="${KLM_WAIT_SLEEP_SECONDS:-0.25}"
READY_ATTEMPTS="${KLM_READY_ATTEMPTS:-60}"
READY_SLEEP_SECONDS="${KLM_READY_SLEEP_SECONDS:-1}"
NODE_BIN="${KLM_NODE_BIN:-}"
PYTHON_BIN="${KLM_PYTHON_BIN:-$ROOT/.venv/bin/python}"
VITE_BIN="${KLM_VITE_BIN:-$ROOT/frontend/node_modules/vite/bin/vite.js}"
SCREEN_BIN="${KLM_SCREEN_BIN:-$(command -v screen 2>/dev/null || true)}"
SERVICE_RUNNER="$ROOT/scripts/run_local_service.sh"
LOG_DIR="${KLM_LOG_DIR:-$ROOT/.local/logs}"
RUN_DIR="${KLM_RUN_DIR:-$ROOT/.local/run}"
BACKEND_PID_FILE="$RUN_DIR/backend-${BACKEND_PORT}.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend-${FRONTEND_PORT}.pid"
BACKEND_STARTED=0
FRONTEND_STARTED=0
STARTUP_COMPLETE=0

mkdir -p "$LOG_DIR" "$RUN_DIR"

if [[ "$BACKEND_PORT" != <-> || "$FRONTEND_PORT" != <-> ]]; then
  printf 'As portas locais precisam ser numéricas.\n' >&2
  exit 1
fi

if [[ -z "$NODE_BIN" ]]; then
  node_candidates=(
    "$ROOT/.local/runtime/node/bin/node"
    /opt/homebrew/opt/node@24/bin/node(N)
    /usr/local/opt/node@24/bin/node(N)
    /tmp/node-v24*/bin/node(N)
  )
  shell_node="$(command -v node 2>/dev/null || true)"
  [[ -n "$shell_node" ]] && node_candidates+=("$shell_node")
  for candidate in "${node_candidates[@]}"; do
    if [[ -x "$candidate" ]] \
      && "$candidate" "$ROOT/frontend/scripts/verify-node-version.mjs" >/dev/null 2>&1; then
      NODE_BIN="$candidate"
      break
    fi
  done
fi

if [[ -z "$NODE_BIN" ]]; then
  printf 'Node.js 24.18.x ou superior na linha 24 não foi encontrado. Defina KLM_NODE_BIN para o executável compatível.\n' >&2
  exit 1
fi

for required_executable in "$NODE_BIN" "$PYTHON_BIN" "$VITE_BIN" "$SCREEN_BIN"; do
  if [[ -z "$required_executable" || ! -x "$required_executable" ]]; then
    printf 'Executável local obrigatório ausente: %s\n' "$required_executable" >&2
    exit 1
  fi
done

if [[ ! -f "$SERVICE_RUNNER" ]]; then
  printf 'Executor local obrigatório ausente: %s\n' "$SERVICE_RUNNER" >&2
  exit 1
fi

if ! "$NODE_BIN" "$ROOT/frontend/scripts/verify-node-version.mjs" >/dev/null; then
  printf 'Runtime Node incompatível: %s\n' "$NODE_BIN" >&2
  exit 1
fi

screen_session_exists() {
  local session="$1"
  local sessions
  sessions="$("$SCREEN_BIN" -ls 2>/dev/null || true)"
  [[ "$sessions" == *".${session}"* ]]
}

terminate_recorded_process() {
  local pid_file="$1"
  local marker="$2"
  local pid command

  [[ -f "$pid_file" ]] || return 0
  pid="$(<"$pid_file")"
  if [[ "$pid" != <-> ]] || ! kill -0 "$pid" >/dev/null 2>&1; then
    rm -f "$pid_file"
    return 0
  fi
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  if [[ "$command" != *"$ROOT"* || "$command" != *"$marker"* ]]; then
    printf 'O PID %s não pertence ao processo KLM esperado; encerramento recusado.\n' "$pid" >&2
    return 1
  fi
  kill -TERM "$pid" >/dev/null 2>&1 || true
  for (( _attempt = 1; _attempt <= WAIT_ATTEMPTS; _attempt++ )); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      rm -f "$pid_file"
      return 0
    fi
    sleep "$WAIT_SLEEP_SECONDS"
  done
  kill -KILL "$pid" >/dev/null 2>&1 || true
  rm -f "$pid_file"
}

stop_session() {
  local session="$1"

  if screen_session_exists "$session"; then
    "$SCREEN_BIN" -S "$session" -X quit >/dev/null 2>&1 || return 1
  fi
  for (( _attempt = 1; _attempt <= WAIT_ATTEMPTS; _attempt++ )); do
    if ! screen_session_exists "$session"; then
      return 0
    fi
    sleep "$WAIT_SLEEP_SECONDS"
  done
  printf 'A sessão local %s não encerrou.\n' "$session" >&2
  return 1
}

wait_for_port_release() {
  local port="$1"
  local service_name="$2"

  for (( _attempt = 1; _attempt <= WAIT_ATTEMPTS; _attempt++ )); do
    if ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      return 0
    fi
    sleep "$WAIT_SLEEP_SECONDS"
  done
  printf '%s ainda ocupa a porta %s; a nova instância não foi iniciada.\n' \
    "$service_name" "$port" >&2
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >&2 || true
  return 1
}

cleanup_partial_start() {
  local exit_status=$?

  trap - EXIT INT TERM
  if (( STARTUP_COMPLETE == 0 )); then
    (( FRONTEND_STARTED == 1 )) && terminate_recorded_process "$FRONTEND_PID_FILE" "vite/bin/vite.js" || true
    (( BACKEND_STARTED == 1 )) && terminate_recorded_process "$BACKEND_PID_FILE" "scripts/run_local.py" || true
    (( FRONTEND_STARTED == 1 )) && stop_session "$FRONTEND_SESSION" || true
    (( BACKEND_STARTED == 1 )) && stop_session "$BACKEND_SESSION" || true
    if (( BACKEND_STARTED == 1 || FRONTEND_STARTED == 1 )); then
      printf 'Inicialização incompleta revertida; nenhum serviço parcial foi mantido.\n' >&2
    fi
  fi
  exit "$exit_status"
}

trap cleanup_partial_start EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

terminate_recorded_process "$FRONTEND_PID_FILE" "vite/bin/vite.js"
terminate_recorded_process "$BACKEND_PID_FILE" "scripts/run_local.py"
stop_session "$FRONTEND_SESSION"
stop_session "$BACKEND_SESSION"
wait_for_port_release "$BACKEND_PORT" "Backend KLM"
wait_for_port_release "$FRONTEND_PORT" "Frontend KLM"

rm -f \
  "$LOG_DIR/backend.stdout.log" \
  "$LOG_DIR/backend.stderr.log" \
  "$LOG_DIR/frontend.stdout.log" \
  "$LOG_DIR/frontend.stderr.log"

BACKEND_STARTED=1
"$SCREEN_BIN" -dmS "$BACKEND_SESSION" \
  /bin/zsh "$SERVICE_RUNNER" \
  "$BACKEND_PID_FILE" "$LOG_DIR/backend.stdout.log" "$LOG_DIR/backend.stderr.log" \
  "$PYTHON_BIN" "$ROOT/scripts/run_local.py" --port "$BACKEND_PORT"

FRONTEND_STARTED=1
"$SCREEN_BIN" -dmS "$FRONTEND_SESSION" \
  /bin/zsh "$SERVICE_RUNNER" \
  "$FRONTEND_PID_FILE" "$LOG_DIR/frontend.stdout.log" "$LOG_DIR/frontend.stderr.log" \
  "$NODE_BIN" "$VITE_BIN" "$ROOT/frontend" \
  --host 127.0.0.1 --port "$FRONTEND_PORT" --strictPort

sleep "$WAIT_SLEEP_SECONDS"
if ! screen_session_exists "$BACKEND_SESSION" || ! screen_session_exists "$FRONTEND_SESSION"; then
  printf 'Um processo local encerrou antes do health-check.\n' >&2
  exit 1
fi

for (( _attempt = 1; _attempt <= READY_ATTEMPTS; _attempt++ )); do
  if curl --silent --fail "http://127.0.0.1:${BACKEND_PORT}/health" >/dev/null \
    && curl --silent --fail "http://127.0.0.1:${FRONTEND_PORT}/app" >/dev/null \
    && curl --silent --fail "http://127.0.0.1:${FRONTEND_PORT}/src/index.css" >/dev/null \
    && curl --silent --fail "http://127.0.0.1:${FRONTEND_PORT}/src/main.tsx" >/dev/null; then
    printf 'KLM Hub ativo:\n'
    printf '  SPA: http://127.0.0.1:%s/app\n' "$FRONTEND_PORT"
    printf '  API: http://127.0.0.1:%s\n' "$BACKEND_PORT"
    STARTUP_COMPLETE=1
    exit 0
  fi
  if ! screen_session_exists "$BACKEND_SESSION" || ! screen_session_exists "$FRONTEND_SESSION"; then
    break
  fi
  sleep "$READY_SLEEP_SECONDS"
done

printf 'Os serviços não ficaram prontos após %s verificações.\n' "$READY_ATTEMPTS" >&2
printf '\nBackend (stderr):\n' >&2
tail -n 40 "$LOG_DIR/backend.stderr.log" >&2 2>/dev/null || true
printf '\nFrontend (stderr):\n' >&2
tail -n 40 "$LOG_DIR/frontend.stderr.log" >&2 2>/dev/null || true
exit 1
