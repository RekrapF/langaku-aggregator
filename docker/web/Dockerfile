FROM python:3.12-slim

# System dependencies (required by psycopg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency file first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Health check (optional)
HEALTHCHECK CMD curl -f http://localhost:8000/healthz || exit 1

# Entry script is specified in docker-compose
