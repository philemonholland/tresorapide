#!/bin/sh
set -eu

wait_for_database() {
  python - <<'PY'
import os
import time

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.db import connections
from django.db.utils import OperationalError

timeout = int(os.getenv("DB_WAIT_TIMEOUT", "60"))
deadline = time.monotonic() + timeout

while True:
    try:
        connections["default"].ensure_connection()
    except OperationalError as exc:
        if time.monotonic() >= deadline:
            raise SystemExit(f"Database not ready after {timeout}s: {exc}") from exc
        print(f"Waiting for database: {exc}")
        time.sleep(2)
    else:
        print("Database connection ready.")
        break
PY
}

if [ "${DATABASE_ENGINE:-django.db.backends.sqlite3}" = "django.db.backends.postgresql" ]; then
  wait_for_database
fi

python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Seed BB demo data on first run (idempotent — skips if data exists)
python manage.py seed_bb_data

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec python manage.py runserver 0.0.0.0:8000 --noreload
