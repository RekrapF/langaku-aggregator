#!/usr/bin/env bash
set -e

# 等待 Postgres 可用
echo "Waiting for Postgres at ${DB_HOST:-db}:${DB_PORT:-5432}..."
until python - <<'PY'
import os, psycopg
host=os.getenv("DB_HOST","db")
port=os.getenv("DB_PORT","5432")
user=os.getenv("DB_USER","postgres")
pwd=os.getenv("DB_PASSWORD","postgres")
name=os.getenv("DB_NAME","appdb")
psycopg.connect(host=host, port=port, user=user, password=pwd, dbname=name).close()
print("ok")
PY
do
  sleep 1
done

# migration
python manage.py migrate --noinput

# 可选：创建超级用户（仅在开发）
# python manage.py createsuperuser --noinput || true

# 启动
# 开发用 runserver；生产建议换成 gunicorn
python manage.py runserver 0.0.0.0:8000
