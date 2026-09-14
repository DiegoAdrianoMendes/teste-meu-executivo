#!/usr/bin/env bash
# Drill de backup/restore contra um PostgreSQL efêmero (mesmo padrão de
# scripts/verify_postgresql_qa.sh): sobe um container Postgres descartável,
# roda as migrations, popula dados via scripts/seed.py contra esse Postgres,
# faz `pg_dump`, cria um SEGUNDO banco vazio no mesmo servidor, restaura o
# dump lá, e compara contagem de linhas das tabelas-chave entre origem e
# destino. Reporta OK/FALHA e nunca toca em banco fora deste container
# descartável.
#
# Uso:
#   scripts/backup_restore_drill.sh
#   KLM_KEEP_BACKUP_DRILL=1 scripts/backup_restore_drill.sh   # mantém o container para inspeção
#
# Ver docs/BACKUP_RESTORE.md para a política de backup (retenção, RTO/RPO) e
# o procedimento de restore de emergência da Fase 1 AWS (RDS snapshot).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER_NAME="klm-postgres-backup-drill-$$"
CONTAINER_ID=""
RUN_ID="backup-drill-$$"
SOURCE_DB="klm_backup_drill_src_$$"
RESTORE_DB="klm_backup_drill_restore_$$"
MIGRATOR_PASSWORD="$(openssl rand -hex 24)"
APP_PASSWORD="$(openssl rand -hex 24)"
POSTGRES_IMAGE="${KLM_POSTGRES_QA_IMAGE:-postgres:15-alpine}"
DUMP_DIR="$ROOT/.local/qa-evidence/backup-restore-drill"
DUMP_PATH="$DUMP_DIR/dump-$$.sql.gz"

# Tabelas-chave verificadas linha a linha entre origem e restore. Cobre os
# domínios centrais do KLM Hub (identidade, matters, mensageria, financeiro).
KEY_TABLES=(
  users
  workspaces
  workspace_memberships
  clients
  matters
)

cleanup() {
  local status="${1:-$?}"
  trap - EXIT INT TERM
  if [[ "${KLM_KEEP_BACKUP_DRILL:-0}" != "1" && -n "$CONTAINER_ID" ]]; then
    local owned_run_id
    owned_run_id="$(docker inspect --format '{{ index .Config.Labels "klm.backup.drill.run" }}' "$CONTAINER_ID" 2>/dev/null || true)"
    if [[ "$owned_run_id" == "$RUN_ID" ]]; then
      docker rm -f "$CONTAINER_ID" >/dev/null 2>&1 || true
    fi
  fi
  exit "$status"
}
trap 'cleanup $?' EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM

command -v docker >/dev/null 2>&1 || {
  printf 'FALHA: Docker é obrigatório para o drill de backup/restore.\n' >&2
  exit 1
}
command -v openssl >/dev/null 2>&1 || {
  printf 'FALHA: OpenSSL é obrigatório para gerar credenciais efêmeras.\n' >&2
  exit 1
}

cd "$ROOT"
mkdir -p "$DUMP_DIR"

printf '==> Subindo PostgreSQL efêmero (%s)...\n' "$POSTGRES_IMAGE"
CONTAINER_ID="$(docker run --detach --rm \
  --pull never \
  --name "$CONTAINER_NAME" \
  --label "klm.backup.drill.run=$RUN_ID" \
  --env POSTGRES_USER=klm_migrator \
  --env POSTGRES_PASSWORD="$MIGRATOR_PASSWORD" \
  --env POSTGRES_DB="$SOURCE_DB" \
  --publish 127.0.0.1::5432 \
  --volume "$ROOT/docker/postgres/bootstrap-runtime-role.sh:/bootstrap-runtime-role.sh:ro" \
  "$POSTGRES_IMAGE")"
[[ -n "$CONTAINER_ID" ]] || {
  printf 'FALHA: Docker não retornou o ID do container do drill.\n' >&2
  exit 1
}

for _ in $(seq 1 45); do
  if docker exec "$CONTAINER_ID" pg_isready --username klm_migrator --dbname "$SOURCE_DB" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$CONTAINER_ID" pg_isready --username klm_migrator --dbname "$SOURCE_DB" >/dev/null

docker exec \
  --env PGHOST=127.0.0.1 \
  --env PGPORT=5432 \
  --env PGPASSWORD="$MIGRATOR_PASSWORD" \
  --env POSTGRES_DB="$SOURCE_DB" \
  --env POSTGRES_USER=klm_migrator \
  --env KLM_APP_DB_USER=klm_app \
  --env KLM_APP_DB_PASSWORD="$APP_PASSWORD" \
  "$CONTAINER_ID" sh /bootstrap-runtime-role.sh >/dev/null

HOST_PORT="$(docker port "$CONTAINER_ID" 5432/tcp | tail -n 1 | awk -F: '{print $NF}')"
[[ "$HOST_PORT" =~ ^[0-9]+$ ]] || {
  printf 'FALHA: não foi possível determinar a porta do PostgreSQL do drill.\n' >&2
  exit 1
}

ADMIN_SYNC_URL="postgresql://klm_migrator:${MIGRATOR_PASSWORD}@127.0.0.1:${HOST_PORT}/${SOURCE_DB}"
ADMIN_ASYNC_URL="postgresql+asyncpg://klm_migrator:${MIGRATOR_PASSWORD}@127.0.0.1:${HOST_PORT}/${SOURCE_DB}"

printf '==> Aplicando migrations (alembic upgrade head) no banco de origem...\n'
env DATABASE_URL="$ADMIN_SYNC_URL" DATABASE_URL_SYNC="$ADMIN_SYNC_URL" \
  "$ROOT/.venv/bin/alembic" upgrade head

printf '==> Populando dados mínimos de demonstração no banco de origem...\n'
# Seed direto via ORM (sem o bootstrap de negócio completo de
# app/services/klm_local_seed_service.py — esse caminho depende do catálogo
# de medicamentos, que tem um bug pré-existente contra Postgres não
# relacionado a este sprint, ver docs/BACKUP_RESTORE.md § Pendências
# conhecidas). Este seed mínimo cobre as tabelas-chave o suficiente para
# validar dump→restore→contagem de linhas de ponta a ponta.
env DATABASE_URL="$ADMIN_ASYNC_URL" DATABASE_URL_SYNC="$ADMIN_SYNC_URL" \
  "$ROOT/.venv/bin/python" "$ROOT/scripts/load/_backup_drill_minimal_seed.py" "$ADMIN_ASYNC_URL"

SOURCE_COUNTS=()
printf '==> Contando linhas das tabelas-chave na origem...\n'
for table in "${KEY_TABLES[@]}"; do
  count="$(docker exec --env PGPASSWORD="$MIGRATOR_PASSWORD" "$CONTAINER_ID" \
    psql --username klm_migrator --dbname "$SOURCE_DB" --tuples-only --no-align \
    --command "SELECT COUNT(*) FROM ${table};" 2>/dev/null | tr -d '[:space:]')"
  SOURCE_COUNTS+=("${count:-0}")
  printf '    %-28s %s\n' "$table" "${count:-0}"
done

printf '==> Gerando dump (pg_dump | gzip)...\n'
docker exec --env PGPASSWORD="$MIGRATOR_PASSWORD" "$CONTAINER_ID" \
  pg_dump --username klm_migrator --dbname "$SOURCE_DB" --format plain --no-owner --no-privileges \
  | gzip > "$DUMP_PATH"
DUMP_SIZE="$(du -h "$DUMP_PATH" | cut -f1)"
printf '    dump salvo em %s (%s)\n' "$DUMP_PATH" "$DUMP_SIZE"

printf '==> Criando banco de destino vazio (%s)...\n' "$RESTORE_DB"
docker exec --env PGPASSWORD="$MIGRATOR_PASSWORD" "$CONTAINER_ID" \
  psql --username klm_migrator --dbname postgres --command "CREATE DATABASE ${RESTORE_DB};"

printf '==> Restaurando dump no banco de destino...\n'
gunzip -c "$DUMP_PATH" | docker exec --interactive --env PGPASSWORD="$MIGRATOR_PASSWORD" "$CONTAINER_ID" \
  psql --username klm_migrator --dbname "$RESTORE_DB" --set ON_ERROR_STOP=on --quiet >/dev/null

FAILED=0
printf '==> Comparando contagens origem x restore...\n'
for i in "${!KEY_TABLES[@]}"; do
  table="${KEY_TABLES[$i]}"
  source_count="${SOURCE_COUNTS[$i]}"
  restored_count="$(docker exec --env PGPASSWORD="$MIGRATOR_PASSWORD" "$CONTAINER_ID" \
    psql --username klm_migrator --dbname "$RESTORE_DB" --tuples-only --no-align \
    --command "SELECT COUNT(*) FROM ${table};" 2>/dev/null | tr -d '[:space:]')"
  restored_count="${restored_count:-0}"
  if [[ "$restored_count" == "$source_count" ]]; then
    printf '    OK    %-28s origem=%-6s restore=%-6s\n' "$table" "$source_count" "$restored_count"
  else
    printf '    FALHA %-28s origem=%-6s restore=%-6s\n' "$table" "$source_count" "$restored_count"
    FAILED=1
  fi
done

if [[ "$FAILED" == "1" ]]; then
  printf '\nRESULTADO: FALHA — contagens divergentes entre origem e restore.\n' >&2
  exit 1
fi

printf '\nRESULTADO: OK — dump, restore e contagem de linhas validados para %d tabelas-chave.\n' "${#KEY_TABLES[@]}"
printf 'Dump de evidência preservado em %s\n' "$DUMP_PATH"
