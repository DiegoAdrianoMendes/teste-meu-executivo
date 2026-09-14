#!/bin/bash
# Entrypoint do container KLM Hub
# Executa migrations pendentes antes de iniciar o servidor

set -e

echo "=== KLM Hub Backend Local ==="
ALEMBIC_TARGET_REVISION="${ALEMBIC_TARGET_REVISION:-head}"
echo "Aplicando migrations do banco de dados até ${ALEMBIC_TARGET_REVISION}..."
alembic upgrade "${ALEMBIC_TARGET_REVISION}"

echo "Iniciando servidor..."
UVICORN_ARGS=(app.main:app --host 0.0.0.0 --port 8000)
if [ "${UVICORN_RELOAD:-false}" = "true" ]; then
  UVICORN_ARGS+=(--reload)
fi
exec uvicorn "${UVICORN_ARGS[@]}"
