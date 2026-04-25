# xgzh-api

XGZH (新股智汇) FastAPI 后端 — 第一刀（First Slice）。

## 当前能力

- `GET /healthz` 健康检查
- `GET /api/v1/ipos?market=HK` 港股近期新股列表（AKShare）
- `GET /api/v1/ipos?market=A` A 股近期新股列表（AKShare）
- `GET /api/v1/ipos/{code}` 新股详情
- `POST /api/v1/agent/diagnose` AI 一键诊断（DeepSeek-V3 SSE 流式）

## 启动

```bash
# 1. 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 配置环境变量
cp .env.example .env
# 至少填入 SILICONFLOW_API_KEY 或 DEEPSEEK_API_KEY

# 3. 安装依赖并启动
uv sync
uv run uvicorn app.main:app --reload --port 8000

# 4. 验证
curl http://localhost:8000/healthz
curl 'http://localhost:8000/api/v1/ipos?market=HK&limit=5'

# 5. 测试 SSE 流式
curl -N -X POST http://localhost:8000/api/v1/agent/diagnose \
  -H 'Content-Type: application/json' \
  -d '{"code":"0700.HK","name":"腾讯控股","question":"分析这只新股的核心风险点"}'
```

## 测试

```bash
uv run pytest
```

## 项目结构

```
app/
├── api/v1/         # 路由
│   ├── ipos.py
│   └── agent.py
├── core/           # 配置、日志
├── services/       # 业务逻辑
├── adapters/       # 外部数据源 (akshare/llm)
├── schemas/        # Pydantic 模型
└── main.py
```

详细规范见 `.cursor/rules/10-backend-fastapi.mdc`。
