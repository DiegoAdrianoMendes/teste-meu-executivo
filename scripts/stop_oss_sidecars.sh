#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.oss.yml"
ENV_FILE="$ROOT/.local/oss.env"
PROJECT_NAME="klm-oss-local"

PROFILE="all"
PROFILE_SET=false

usage() {
  cat <<'EOF'
Usage: scripts/stop_oss_sidecars.sh [channels|collaboration|automation|crm|all]

Stops the selected profile without deleting containers or named volumes.
EOF
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

for argument in "$@"; do
  case "$argument" in
    channels | collaboration | automation | crm | all)
      [[ "$PROFILE_SET" == false ]] || die "only one profile selector is allowed"
      PROFILE="$argument"
      PROFILE_SET=true
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

command -v docker >/dev/null 2>&1 || die "required command not found: docker"
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"

if [[ ! -f "$ENV_FILE" ]]; then
  printf 'No %s file exists; there is no managed OSS runtime to stop.\n' "$ENV_FILE"
  exit 0
fi
[[ ! -L "$ENV_FILE" ]] || die ".local/oss.env must not be a symlink"

docker info >/dev/null 2>&1 || die "Docker daemon is not available"

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

"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" stop --timeout 60

printf 'Stopped KLM OSS profile %s. Named volumes were preserved.\n' "$PROFILE"
