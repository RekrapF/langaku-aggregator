```bash
## Quick Start

# 1) Clone
git clone https://github.com/RekrapF/langaku-aggregator.git
cd langaku-aggregator

# 2) Prepare environment variables
cp .env.sample .env
#Edit .env if you need to change the password or database name

# 3) Start with one command
docker compose up -d --build

# 4) Verify
curl http://localhost:8000/api/users/test/summary?from=2025-10-01T00:00:00Z&to=2025-10-02T00:00:00Z&granularity=day
#On the first run, since there’s no data yet, totals/averages will return values close to 0 (as placeholder text)
