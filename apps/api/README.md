# xgzh-api

XGZH (新股智汇) FastAPI 后端。

## 当前能力（Sprint 0 + INFRA-001）

API:

- `GET /healthz` 健康检查
- `GET /api/v1/ipos?market=HK` 港股近期新股列表（akshare 暂用 seed，HKEX/Futu 接入排在 Sprint 2）
- `GET /api/v1/ipos?market=A` A 股近期新股列表（AKShare `stock_new_ipo_cninfo`）
- `GET /api/v1/ipos/{code}` 新股详情
- `POST /api/v1/agent/diagnose` AI 一键诊断（DeepSeek-V3 SSE 流式）

Schema（Alembic `0001_init`，PG 16 + pgvector 0.8.2）:

- `users` / `auth_sessions` / `invite_codes`
- `ipos` / `ipo_documents`（embedding `vector(1024)` + HNSW 索引）
- `user_favorites` / `push_tokens`

## 启动（首次）

```bash
# 1. 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 配置环境变量
cp .env.example .env
# 至少填入 SILICONFLOW_API_KEY 或 DEEPSEEK_API_KEY

# 3. 安装 Python 依赖
uv sync

# 4. 起 Postgres + Redis（任选一种）
# 4a. Docker 方案（推荐, 与 spec/05 PG 16 + pgvector 一致）
docker compose -f ../../infra/docker-compose.yml up -d postgres redis

# 4b. 本地 brew 方案（已装 PG 14 时）
#    需要先把 pgvector 装到 PG 14：
#    git clone --branch v0.8.2 https://github.com/pgvector/pgvector.git /tmp/pgvector
#    cd /tmp/pgvector
#    CPATH="$(xcrun --show-sdk-path)/usr/include" \
#      LIBRARY_PATH="$(xcrun --show-sdk-path)/usr/lib" \
#      PG_CONFIG=$(brew --prefix postgresql@14)/bin/pg_config make install
#    然后建库 + 启用扩展：
#    psql -U postgres -c "CREATE ROLE xgzh LOGIN PASSWORD 'xgzh_dev_pass';"
#    psql -U postgres -c "CREATE DATABASE xgzh OWNER xgzh;"
#    psql -U postgres -d xgzh -c "CREATE EXTENSION pgcrypto; CREATE EXTENSION vector;"

# 5. 跑迁移建表
uv run alembic upgrade head

# 6. 启动 API
uv run uvicorn app.main:app --reload --port 8000

# 7. 验证
curl http://localhost:8000/healthz
curl 'http://localhost:8000/api/v1/ipos?market=HK&limit=5'

# 8. SSE 流式
curl -N -X POST http://localhost:8000/api/v1/agent/diagnose \
  -H 'Content-Type: application/json' \
  -d '{"code":"0700.HK","name":"腾讯控股","question":"分析这只新股的核心风险点"}'
```

## 数据库迁移

```bash
# 查看当前版本
uv run alembic current

# 升级 / 降级
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic downgrade base

# 临时连别的库（如 staging）
uv run alembic -x url='postgresql+asyncpg://user:pass@host/db' upgrade head

# 新增迁移：先在 app/db/models/ 加 ORM, 然后
uv run alembic revision --autogenerate -m "add foo table"
# ⚠️ autogenerate 不会自动产生 HNSW/GIN 之类自定义索引, 必须人工补在版本文件里
```

## 测试

```bash
# 单测（无 DB 集成测试自动跳过）
uv run pytest

# 含 DB 集成测试（迁移 up/down/idempotent）
# 先建测试库 xgzh_test:
psql -U postgres -c "CREATE DATABASE xgzh_test OWNER xgzh;"
psql -U postgres -d xgzh_test -c "CREATE EXTENSION pgcrypto; CREATE EXTENSION vector;"

XGZH_TEST_DATABASE_URL='postgresql+asyncpg://xgzh:xgzh_dev_pass@localhost:5432/xgzh_test' \
  uv run pytest -q
```

## 项目结构

```
app/
├── api/v1/         # 路由 (ipos / agent)
├── core/           # 配置、日志
├── services/       # 业务逻辑
├── adapters/       # 外部数据源 (akshare / llm)
├── schemas/        # Pydantic 模型
├── db/             # SQLAlchemy 2.0 async Base + ORM models
│   ├── base.py
│   └── models/     # users, auth, invite, ipo, push 等
└── main.py
alembic/
├── env.py
└── versions/       # 0001_init_core_schema.py …
```

详见 `.cursor/rules/10-backend-fastapi.mdc` 与 `.cursor/rules/40-database.mdc`。
