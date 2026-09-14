#!/bin/sh
set -eu

umask 027

mode="${1:-api}"

case "$mode" in
  migrate|api|worker)
    python /opt/chattermate/runtime_preflight.py validate
    python /opt/chattermate/runtime_preflight.py wait
    ;;
  *)
    exec "$@"
    ;;
esac

case "$mode" in
  migrate)
    echo "Applying ChatterMate database migrations..."
    exec alembic upgrade head
    ;;
  api)
    echo "Starting ChatterMate API without startup model preloading..."
    exec gunicorn app.main:app \
      --bind "0.0.0.0:${PORT:-8000}" \
      --workers "${WORKERS:-1}" \
      --worker-class uvicorn.workers.UvicornWorker \
      --worker-tmp-dir /tmp \
      --timeout "${TIMEOUT:-120}" \
      --graceful-timeout "${GRACEFUL_TIMEOUT:-30}" \
      --keep-alive "${KEEP_ALIVE:-5}" \
      --log-level "${LOG_LEVEL:-info}" \
      --access-logfile - \
      --error-logfile -
    ;;
  worker)
    echo "Starting ChatterMate knowledge and FAQ worker without startup model preloading..."
    exec python -m app.workers.knowledge_processor
    ;;
esac
