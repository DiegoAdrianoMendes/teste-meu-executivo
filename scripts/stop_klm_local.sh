#!/bin/zsh

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_PORT="${KLM_BACKEND_PORT:-8010}"
FRONTEND_PORT="${KLM_FRONTEND_PORT:-4173}"
BACKEND_SESSION="${KLM_BACKEND_SCREEN_SESSION:-klm-hub-backend-${BACKEND_PORT}}"
FRONTEND_SESSION="${KLM_FRONTEND_SCREEN_SESSION:-klm-hub-frontend-${FRONTEND_PORT}}"
SCREEN_BIN="${KLM_SCREEN_BIN:-$(command -v screen 2>/dev/null || true)}"
WAIT_ATTEMPTS="${KLM_WAIT_ATTEMPTS:-40}"
WAIT_SLEEP_SECONDS="${KLM_WAIT_SLEEP_SECONDS:-0.25}"
RUN_DIR="${KLM_RUN_DIR:-$ROOT/.local/run}"
BACKEND_PID_FILE="$RUN_DIR/backend-${BACKEND_PORT}.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend-${FRONTEND_PORT}.pid"
EXIT_STATUS=0

screen_session_exists() {
  local session="$1"
  local sessions
  [[ -n "$SCREEN_BIN" ]] || return 1
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
    if ! "$SCREEN_BIN" -S "$session" -X quit >/dev/null 2>&1; then
      printf 'Falha ao solicitar a parada de %s.\n' "$session" >&2
      return 1
    fi
  fi
  for (( _attempt = 1; _attempt <= WAIT_ATTEMPTS; _attempt++ )); do
    if ! screen_session_exists "$session"; then
      return 0
    fi
    sleep "$WAIT_SLEEP_SECONDS"
  done
  printf 'A sessão %s continua ativa após a solicitação de parada.\n' "$session" >&2
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
  printf '%s ainda ocupa a porta %s.\n' "$service_name" "$port" >&2
  return 1
}

terminate_recorded_process "$FRONTEND_PID_FILE" "vite/bin/vite.js" || EXIT_STATUS=1
terminate_recorded_process "$BACKEND_PID_FILE" "scripts/run_local.py" || EXIT_STATUS=1
stop_session "$FRONTEND_SESSION" || EXIT_STATUS=1
stop_session "$BACKEND_SESSION" || EXIT_STATUS=1
wait_for_port_release "$FRONTEND_PORT" "Frontend KLM" || EXIT_STATUS=1
wait_for_port_release "$BACKEND_PORT" "Backend KLM" || EXIT_STATUS=1

if (( EXIT_STATUS != 0 )); then
  printf 'Os serviços locais do KLM Hub não foram encerrados por completo.\n' >&2
  exit "$EXIT_STATUS"
fi

printf 'Serviços locais do KLM Hub encerrados.\n'
