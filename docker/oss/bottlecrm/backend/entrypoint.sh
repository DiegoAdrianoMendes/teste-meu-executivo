#!/bin/sh
set -eu

mode="${1:-api}"

python - <<'PY'
import os
import socket
import time

targets = (
    (os.environ["DBHOST"], int(os.environ.get("DBPORT", "5432"))),
    ("bottlecrm-redis", 6379),
)
deadline = time.monotonic() + int(os.environ.get("BOTTLECRM_STARTUP_TIMEOUT", "300"))
for host, port in targets:
    while True:
        try:
            with socket.create_connection((host, port), timeout=3):
                break
        except OSError:
            if time.monotonic() >= deadline:
                raise SystemExit(f"dependency did not become ready: {host}:{port}")
            time.sleep(2)
PY

case "$mode" in
  init)
    python manage.py migrate --noinput
    python manage.py create_default_admin
    python /opt/bottlecrm/bootstrap_org.py
    python manage.py collectstatic --noinput
    python manage.py manage_rls --verify-user
    ;;
  api)
    exec gunicorn crm.wsgi:application \
      --bind "0.0.0.0:${PORT:-8000}" \
      --workers "${BOTTLECRM_WEB_WORKERS:-2}" \
      --threads "${BOTTLECRM_WEB_THREADS:-2}" \
      --timeout "${BOTTLECRM_WEB_TIMEOUT:-120}" \
      --access-logfile - \
      --error-logfile -
    ;;
  worker)
    exec celery -A crm worker --loglevel=info --concurrency="${BOTTLECRM_CELERY_CONCURRENCY:-2}"
    ;;
  beat)
    exec celery -A crm beat --loglevel=info --schedule=/tmp/celerybeat-schedule
    ;;
  *)
    exec "$@"
    ;;
esac
