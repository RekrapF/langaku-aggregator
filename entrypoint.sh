#!/usr/bin/env sh
set -eu

DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-5432}"

echo "Waiting for Postgres at ${DB_HOST}:${DB_PORT}..."

# Prefer pg_isready if available in the container
if command -v pg_isready >/dev/null 2>&1; then
  until pg_isready -h "$DB_HOST" -p "$DB_PORT" -q; do
    sleep 1
  done
else
  # Fallback: use Python to probe TCP readiness (no netcat dependency)
  python - <<'PY'
import os, socket, time
h = os.environ.get("DB_HOST", "db")
p = int(os.environ.get("DB_PORT", "5432"))
while True:
    try:
        s = socket.create_connection((h, p), timeout=1)
        s.close()
        break
    except Exception:
        time.sleep(1)
print("Postgres is ready")
PY
fi

# Run migrations
python manage.py migrate --noinput

# Hand off to CMD/command
exec "$@"