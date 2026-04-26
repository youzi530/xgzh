# 新股智汇 XGZH (IPO Agent)

> 港股 / A 股打新 + AI 分析 + 跨境 CRS 报税向导 + 券商对比 一站式平台

[![status](https://img.shields.io/badge/status-MVP_dev-orange)]()
[![stack](https://img.shields.io/badge/stack-UniApp_+_FastAPI_+_DeepSeek-blue)]()

## 🏃 当前迭代

- **第一刀（First Slice）已跑通**：`/healthz` + `/api/v1/ipos` (HK seed + A-share AKShare) + `/api/v1/agent/diagnose` SSE
- **Sprint 1 进度**：[backlog](spec/08-sprint-1-backlog.md) — 16 个 PR-sized issue
  - ✅ **INFRA-001**：Alembic + 7 张表 + pgvector(1024)
  - ✅ **INFRA-002**：Redis cache 封装（`@cached` / `@rate_limit` Lua 原子 + InMemory fake）
  - ✅ **BE-001**：手机号 OTP 发送（`POST /api/v1/auth/otp/send`，Mock SMS + 60s 限流 + Redis 落库）
  - ✅ **BE-002**：OTP 校验 + 注册/登录 + JWT 颁发（`POST /api/v1/auth/login/phone`，HS256 access 30min + refresh 30d，verify 5/5min 限流）
  - ✅ **BE-003**：JWT 中间件 + `current_user` 依赖（`GET /api/v1/me`，6 种 401 reason，typ 强制隔离，`get_optional_user` 支持匿名混合接口）
  - ✅ **BE-004**：Refresh token rotation + 黑名单（`POST /api/v1/auth/refresh` + `POST /api/v1/auth/logout`，旧 refresh 一次性，access/refresh 双拉黑，jti 粒度）
  - ✅ **BE-005**：微信小程序登录（`POST /api/v1/auth/login/wechat-mp`，`code2Session` 拿 openid/unionid，unionid > openid 优先，错误码二分映射 401/502/503，session_key 不落库）
  - ✅ **BE-006**：邀请码生成 + 绑定（注册原子落 `invite_codes` 行；`POST /api/v1/invite/bind` 一次性 + 自禁 + 并发安全 + 7 类错误码）
  - ✅ **BE-007**：IPO 表持久化 + AKShare 调度入库（APScheduler `AsyncIOScheduler` lifespan 启停；启动后立即一次 + cron 08:00/20:00 Asia/Shanghai；`upsert_ipos` PG `ON CONFLICT (code, market) DO UPDATE` 一条 SQL；`COALESCE` 兜底防擦数据；`run_ingest_a_job` 永不抛）
  - ✅ **BE-008**：`GET /ipos` 切回数据库 + 筛选 + 分页 + Redis 缓存（A 股走 `ipos` 表；HK 仍 seed；`status`/`industry` 筛选；`page`/`size` 分页 1-100；`listing_date DESC NULLS LAST` 排序；`@cached(ttl=600s, namespace="ipos:list")` 含 5 元组参数 hash）
  - 进行中：BE-009 (`GET /ipos/{code}` 字段聚合 / 多源 merge)
- **后端测试**：
  - 无 DB：`cd apps/api && uv run pytest -q` ⇒ 87 passed / 86 skipped
  - 有 DB：`XGZH_TEST_DATABASE_URL=... uv run pytest -q` ⇒ 173 passed

## 📖 设计文档

完整产品 / 技术 / 商业 / 合规设计在 [`spec/`](./spec/) 下：

| 章节 | 内容 |
|------|------|
| [01](./spec/01-business%20prompt.md) | 业务诉求原稿 |
| [02](./spec/02-产品整体架构与模块划分.md) | 思维导图式架构与优先级 |
| [03](./spec/03-核心功能模块深度解析.md) | 7 大模块的用户流 / UI / 字段 |
| [04](./spec/04-AI-Agent与数据源技术落地方案.md) | 模型选型 / RAG / Tool Use / 数据源 |
| [05](./spec/05-全栈技术栈选型.md) | UniApp + FastAPI + Postgres |
| [06](./spec/06-商业化变现与合规避险.md) | CPA / 订阅 / 法律隔离 |
| [07](./spec/07-MVP开发清单与排期.md) | MVP 10-12 周排期 |
| [08](./spec/08-sprint-1-backlog.md) | Sprint 1 PR-Ready Backlog（16 issue） |

## 🏗️ 仓库结构

```
xgzh/
├── apps/
│   ├── api/      # FastAPI 业务后端
│   ├── agent/    # AI Agent 服务（RAG / Tool Use，第二阶段拆分）
│   ├── mp/       # UniApp 客户端（小程序 + App + H5）
│   └── admin/    # 运营后台（占位）
├── packages/
│   ├── shared-types/   # 跨端共享类型
│   ├── prompts/        # 版本化 Prompt
│   └── eval/           # AI 离线评测集
├── infra/
│   └── docker-compose.yml   # PG + Redis + Meilisearch 一键起
├── spec/                    # 产品设计文档
├── .cursor/                 # AI 助手 rules + hooks
└── AGENTS.md                # AI 助手最高铁律
```

## 🚀 快速开始（First Slice）

第一刀 = 端到端跑通：UniApp 列表页 → FastAPI → AKShare + DeepSeek → SSE 流式输出。

### 0. 准备凭证

```bash
cp apps/api/.env.example apps/api/.env
# 编辑 .env，至少填入：
#   SILICONFLOW_API_KEY=sk-...     # 推荐，硅基流动一站式接入
#   或 DEEPSEEK_API_KEY=sk-...
#   TUSHARE_TOKEN=...              # 可选，AKShare 不需要 Token
```

### 1. 启动基础设施（可选，第一刀不强制）

```bash
cd infra && docker compose up -d
```

### 2. 启动后端

```bash
cd apps/api
# 安装 uv（如未安装）：curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

健康检查：

```bash
curl http://localhost:8000/healthz
curl http://localhost:8000/api/v1/ipos?market=HK
```

### 3. 启动小程序

```bash
cd apps/mp
pnpm install
# 微信开发者工具打开 apps/mp 目录（构建产物在 dist/dev/mp-weixin）
pnpm dev:mp-weixin
```

或用 HBuilderX 打开 `apps/mp` 直接运行到微信小程序模拟器。

## 🧪 第一刀的 3 个验证目标

- [ ] AKShare 能拉到港股近期 IPO 列表
- [ ] DeepSeek-V3 SSE 流式输出在小程序端能渲染
- [ ] 端到端往返延迟 P95 < 3s

## 🛡️ 合规与安全

请仔细阅读 [`AGENTS.md`](./AGENTS.md) 与 [`spec/06`](./spec/06-商业化变现与合规避险.md)。本项目严格定位为**信息聚合工具**，**不构成投资 / 税务 / 法律建议**。

## 📝 License

私有仓库，All Rights Reserved。
