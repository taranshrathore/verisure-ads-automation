#!/bin/sh
# Production Docker Hardening — container entrypoint.
# Roles: api (migrate + uvicorn) | worker (publish worker only).
# Migrations run only in the API role so they are never duplicated.

set -eu

ROLE="${1:-api}"

wait_for_database() {
  if [ -z "${DATABASE_URL:-}" ]; then
    echo "DATABASE_URL is required" >&2
    exit 1
  fi

  python - <<'PY'
import os
import sys
import time

import psycopg

raw = os.environ["DATABASE_URL"]
url = raw.replace("postgresql+psycopg://", "postgresql://", 1)

deadline = time.time() + 60
last_error = None
while time.time() < deadline:
    try:
        with psycopg.connect(url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        print("database_ready", flush=True)
        raise SystemExit(0)
    except Exception as exc:  # noqa: BLE001 — retry until deadline
        last_error = exc
        time.sleep(1)

print(f"database_not_ready: {last_error}", file=sys.stderr, flush=True)
raise SystemExit(1)
PY
}

echo "starting_role=${ROLE}"

wait_for_database

case "${ROLE}" in
  api)
    echo "running_migrations"
    alembic upgrade head
    echo "starting_uvicorn"
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
    ;;
  worker)
    echo "starting_publish_worker"
    exec python -m app.orchestration.publish_worker
    ;;
  *)
    echo "Unknown role '${ROLE}'. Use: api | worker" >&2
    exit 1
    ;;
esac
