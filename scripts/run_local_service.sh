#!/bin/zsh

set -euo pipefail

PID_PATH="$1"
STDOUT_PATH="$2"
STDERR_PATH="$3"
shift 3

printf '%s\n' "$$" >"${PID_PATH}.tmp"
mv -f "${PID_PATH}.tmp" "$PID_PATH"

exec "$@" >>"$STDOUT_PATH" 2>>"$STDERR_PATH"
