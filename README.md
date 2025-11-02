## Quick Start

```bash
# 1) 克隆
git clone https://github.com/<your-username>/<repo-name>.git
cd <repo-name>

# 2) 准备环境变量
cp .env.sample .env
# 如需修改密码/库名，请编辑 .env

# 3) 一键启动
docker compose up -d --build

# 4) 验证
curl http://localhost:8000/api/users/test/summary?from=2025-10-01T00:00:00Z&to=2025-10-02T00:00:00Z&granularity=day
# 首次空数据会返回 totals/averages 近似 0（小值文案）
