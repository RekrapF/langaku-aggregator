FROM python:3.12-slim

# 系统依赖（psycopg需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先拷贝依赖文件以利用缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝源码
COPY . .

# 健康检查（可选）
HEALTHCHECK CMD curl -f http://localhost:8000/healthz || exit 1

# 入口脚本在 compose 里指定
