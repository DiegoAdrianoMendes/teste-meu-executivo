#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER_NAME="klm-postgres-qa-$$"
CONTAINER_ID=""
QA_RUN_ID="postgresql-qa-$$"
DATABASE_NAME="klm_legal_hub_qa_$$"
MASTER_PASSWORD="$(openssl rand -hex 24)"
MIGRATOR_PASSWORD="$(openssl rand -hex 24)"
APP_PASSWORD="$(openssl rand -hex 24)"
POSTGRES_IMAGE="${KLM_POSTGRES_QA_IMAGE:-postgres:17-alpine}"
POSTGRES_PULL_POLICY="${KLM_POSTGRES_QA_PULL_POLICY:-missing}"
JUNIT_PATH="$ROOT/.local/qa-evidence/postgresql-qa/junit.xml"
POSTGRES_LOG_PATH="$ROOT/.local/qa-evidence/postgresql-qa/container.log"
POSTGRES_ENV_PATH="${KLM_POSTGRES_QA_ENV_FILE:-}"
RUN_ALL=0
PYTEST_TARGETS=()

if [[ -n "$POSTGRES_ENV_PATH" && "$POSTGRES_ENV_PATH" != /* ]]; then
  POSTGRES_ENV_PATH="$ROOT/$POSTGRES_ENV_PATH"
fi

resolve_tool() {
  local explicit="$1"
  local local_candidate="$2"
  local command_name="$3"

  if [[ -n "$explicit" ]]; then
    printf '%s\n' "$explicit"
  elif [[ -x "$local_candidate" ]]; then
    printf '%s\n' "$local_candidate"
  else
    command -v "$command_name" || return 1
  fi
}

ALEMBIC_BIN="$(resolve_tool "${KLM_ALEMBIC_BIN:-}" "$ROOT/.venv/bin/alembic" alembic)" || {
  printf 'Alembic is required for the PostgreSQL QA suite.\n' >&2
  exit 1
}
PYTEST_BIN="$(resolve_tool "${KLM_PYTEST_BIN:-}" "$ROOT/.venv/bin/pytest" pytest)" || {
  printf 'pytest is required for the PostgreSQL QA suite.\n' >&2
  exit 1
}
PYTHON_BIN="$(resolve_tool "${KLM_PYTHON_BIN:-}" "$ROOT/.venv/bin/python" python)" || {
  printf 'Python is required for the PostgreSQL QA suite.\n' >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --all)
      RUN_ALL=1
      ;;
    --junitxml=*)
      JUNIT_PATH="${1#--junitxml=}"
      [[ -n "$JUNIT_PATH" ]] || {
        printf 'The JUnit path cannot be empty.\n' >&2
        exit 2
      }
      ;;
    --pytest-target=*)
      target="${1#--pytest-target=}"
      [[ -n "$target" ]] || {
        printf 'The pytest target cannot be empty.\n' >&2
        exit 2
      }
      PYTEST_TARGETS+=("$target")
      ;;
    *)
      printf 'Unknown PostgreSQL QA option: %s\n' "$1" >&2
      exit 2
      ;;
  esac
  shift
done

if [[ "$RUN_ALL" == "1" && "${#PYTEST_TARGETS[@]}" -gt 0 ]]; then
  printf 'Use either --all or --pytest-target, never both.\n' >&2
  exit 2
fi

POSTGRES_TESTS=(
  tests/integration/test_approval_concurrency_postgresql.py
  tests/integration/test_auth_rate_limit_concurrency_postgresql.py
  tests/integration/test_database_role_bootstrap_postgresql.py
  tests/integration/test_email_ingest_concurrency_postgresql.py
  tests/integration/test_entitlement_concurrency_postgresql.py
  tests/integration/test_finance_cost_center_rollup_postgresql.py
  tests/integration/test_identity_registration_postgresql.py
  tests/integration/test_local_billing_concurrency_postgresql.py
  tests/integration/test_local_storage_rls_postgresql.py
  tests/integration/test_meeting_concurrency_postgresql.py
  tests/integration/test_migration_data_loss_preflights_postgresql.py
  tests/integration/test_postgresql_rls.py
  tests/integration/test_task_collaboration_rls_postgresql.py
  tests/integration/test_whatsapp_webhook_concurrency_postgresql.py
  tests/db/test_legal_crm_rls_postgresql.py
)

cleanup() {
  local status="${1:-$?}"
  trap - EXIT INT TERM
  if [[ "$status" != "0" && -n "$CONTAINER_ID" ]]; then
    mkdir -p "$(dirname "$POSTGRES_LOG_PATH")"
    docker logs "$CONTAINER_ID" >"$POSTGRES_LOG_PATH" 2>&1 || true
  fi
  if [[ ( "$status" != "0" || "${KLM_KEEP_POSTGRES_QA:-0}" != "1" ) && -n "$CONTAINER_ID" ]]; then
    local owned_run_id
    owned_run_id="$(docker inspect --format '{{ index .Config.Labels "klm.postgresql.qa.run" }}' "$CONTAINER_ID" 2>/dev/null || true)"
    if [[ "$owned_run_id" == "$QA_RUN_ID" ]]; then
      docker rm -f "$CONTAINER_ID" >/dev/null 2>&1 || true
    fi
  fi
  if [[ ( "$status" != "0" || "${KLM_KEEP_POSTGRES_QA:-0}" != "1" ) && -n "$POSTGRES_ENV_PATH" ]]; then
    rm -f "$POSTGRES_ENV_PATH"
  fi
  exit "$status"
}
trap 'cleanup $?' EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM

command -v docker >/dev/null 2>&1 || {
  printf 'Docker is required for the PostgreSQL QA suite.\n' >&2
  exit 1
}
command -v openssl >/dev/null 2>&1 || {
  printf 'OpenSSL is required to generate ephemeral QA credentials.\n' >&2
  exit 1
}

cd "$ROOT"
CONTAINER_ID="$(docker run --detach --rm \
  --pull "$POSTGRES_PULL_POLICY" \
  --name "$CONTAINER_NAME" \
  --label "klm.postgresql.qa.run=$QA_RUN_ID" \
  --env POSTGRES_USER=klm_master \
  --env POSTGRES_PASSWORD="$MASTER_PASSWORD" \
  --env POSTGRES_DB="$DATABASE_NAME" \
  --publish 127.0.0.1::5432 \
  "$POSTGRES_IMAGE")"
[[ -n "$CONTAINER_ID" ]] || {
  printf 'Docker did not return the PostgreSQL QA container ID.\n' >&2
  exit 1
}

# The official image briefly starts a bootstrap postmaster before stopping it
# and launching the final server.  pg_isready alone can therefore produce a
# false green while the requested database is still being created.  Require a
# real query against that database twice, separated by a short interval.
stable_checks=0
for _ in $(seq 1 60); do
  if docker exec "$CONTAINER_ID" \
    psql --username klm_master --dbname "$DATABASE_NAME" \
      --tuples-only --no-align --command 'SELECT current_database()' \
      2>/dev/null | grep -qx "$DATABASE_NAME"; then
    stable_checks=$((stable_checks + 1))
    if [[ "$stable_checks" -ge 2 ]]; then
      break
    fi
  else
    stable_checks=0
  fi
  sleep 1
done

[[ "$stable_checks" -ge 2 ]] || {
  printf 'PostgreSQL QA database did not become stably queryable.\n' >&2
  exit 1
}

POSTGRES_SERVER_VERSION_NUM="$(
  docker exec "$CONTAINER_ID" \
    psql \
      --username klm_master \
      --dbname "$DATABASE_NAME" \
      --tuples-only \
      --no-align \
      --command 'SHOW server_version_num'
)"
[[ "$POSTGRES_SERVER_VERSION_NUM" =~ ^17[0-9]{4}$ ]] || {
  printf 'PostgreSQL QA requires major version 17; observed server_version_num=%s.\n' \
    "$POSTGRES_SERVER_VERSION_NUM" >&2
  exit 1
}

HOST_PORT="$(docker port "$CONTAINER_ID" 5432/tcp | tail -n 1 | awk -F: '{print $NF}')"
[[ "$HOST_PORT" =~ ^[0-9]+$ ]] || {
  printf 'Could not determine the PostgreSQL QA port.\n' >&2
  exit 1
}

MASTER_ASYNC_URL="postgresql+asyncpg://klm_master:${MASTER_PASSWORD}@127.0.0.1:${HOST_PORT}/${DATABASE_NAME}"
MIGRATOR_ASYNC_URL="postgresql+asyncpg://klm_migrator:${MIGRATOR_PASSWORD}@127.0.0.1:${HOST_PORT}/${DATABASE_NAME}"
APP_ASYNC_URL="postgresql+asyncpg://klm_app:${APP_PASSWORD}@127.0.0.1:${HOST_PORT}/${DATABASE_NAME}"
APP_SYNC_URL="postgresql+psycopg2://klm_app:${APP_PASSWORD}@127.0.0.1:${HOST_PORT}/${DATABASE_NAME}"

if [[ -n "$POSTGRES_ENV_PATH" ]]; then
  mkdir -p "$(dirname "$POSTGRES_ENV_PATH")"
  umask 077
  {
    printf 'KLM_POSTGRES_QA_CONTAINER_ID=%q\n' "$CONTAINER_ID"
    printf 'KLM_POSTGRES_QA_RUN_ID=%q\n' "$QA_RUN_ID"
    printf 'KLM_POSTGRES_QA_APP_ASYNC_URL=%q\n' "$APP_ASYNC_URL"
    printf 'KLM_POSTGRES_QA_APP_SYNC_URL=%q\n' "$APP_SYNC_URL"
  } >"$POSTGRES_ENV_PATH"
  chmod 0600 "$POSTGRES_ENV_PATH"
  if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
    printf '::add-mask::%s\n' "$APP_PASSWORD"
  fi
fi

mkdir -p "$(dirname "$JUNIT_PATH")"

KLM_RELEASE_MODE=greenfield-prepare \
  "$ROOT/scripts/verify_greenfield_v1_mode.sh"

env \
  KLM_DB_BOOTSTRAP_HOST=127.0.0.1 \
  KLM_DB_BOOTSTRAP_PORT="$HOST_PORT" \
  KLM_DB_BOOTSTRAP_DATABASE="$DATABASE_NAME" \
  KLM_DB_BOOTSTRAP_MASTER_USERNAME=klm_master \
  KLM_DB_BOOTSTRAP_MASTER_PASSWORD="$MASTER_PASSWORD" \
  KLM_DB_BOOTSTRAP_MIGRATOR_USERNAME=klm_migrator \
  KLM_DB_BOOTSTRAP_MIGRATOR_PASSWORD="$MIGRATOR_PASSWORD" \
  KLM_DB_BOOTSTRAP_RUNTIME_USERNAME=klm_app \
  KLM_DB_BOOTSTRAP_RUNTIME_PASSWORD="$APP_PASSWORD" \
  KLM_DB_BOOTSTRAP_SSLMODE=disable \
  "$PYTHON_BIN" -m scripts.run_postgresql_migration_cycle \
    --alembic-bin "$ALEMBIC_BIN"

if [[ "${#PYTEST_TARGETS[@]}" -gt 0 ]]; then
  env \
    KLM_TEST_POSTGRES_MASTER_URL="$MASTER_ASYNC_URL" \
    KLM_TEST_POSTGRES_ADMIN_URL="$MIGRATOR_ASYNC_URL" \
    KLM_TEST_POSTGRES_APP_URL="$APP_ASYNC_URL" \
    KLM_TEST_POSTGRES_QA_DISPOSABLE=1 \
    "$PYTEST_BIN" -q \
      "--junitxml=$JUNIT_PATH" \
      "${PYTEST_TARGETS[@]}"
elif [[ "$RUN_ALL" == "1" ]]; then
  env \
    KLM_TEST_POSTGRES_MASTER_URL="$MASTER_ASYNC_URL" \
    KLM_TEST_POSTGRES_ADMIN_URL="$MIGRATOR_ASYNC_URL" \
    KLM_TEST_POSTGRES_APP_URL="$APP_ASYNC_URL" \
    KLM_TEST_POSTGRES_QA_DISPOSABLE=1 \
    "$PYTEST_BIN" -q "--junitxml=$JUNIT_PATH"
else
  env \
    KLM_TEST_POSTGRES_MASTER_URL="$MASTER_ASYNC_URL" \
    KLM_TEST_POSTGRES_ADMIN_URL="$MIGRATOR_ASYNC_URL" \
    KLM_TEST_POSTGRES_APP_URL="$APP_ASYNC_URL" \
    KLM_TEST_POSTGRES_QA_DISPOSABLE=1 \
    "$PYTEST_BIN" -q \
      "--junitxml=$JUNIT_PATH" \
      "${POSTGRES_TESTS[@]}"
fi

"$PYTHON_BIN" scripts/verify_junit_clean.py "$JUNIT_PATH"

printf 'PostgreSQL 17 QA completed with runtime RLS enabled and zero skipped tests.\n'
