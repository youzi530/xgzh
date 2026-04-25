# AGENTS.md — XGZH 项目最高优先级铁律

> 任何 AI 代码助手在本仓库工作前必读。违反任何一条 = 必须停下来跟用户确认。

---

## 1. 业务背景速览（30 秒）

- 产品名：**新股智汇 (XGZH)** — 港股/A 股打新 + AI 分析 + CRS 报税向导 + 券商对比
- 形态：UniApp 小程序 + App + H5（一套代码）
- 商业模式：Freemium + CPA/CPS 券商返佣
- 完整设计文档：**`spec/01~07*.md` 必读**（特别是 02 架构、04 AI 方案、05 技术栈）

---

## 2. 不可逾越的 5 条铁律

### 🚨 R1 - 合规中立（最高优先级）

- AI 输出**严禁**包含："建议买入/满仓/重仓/必涨/稳赚/抄底/保本/保收益/all in"
- 所有用户可见的金融分析必须以"以上为客观分析，不构成投资建议"结尾
- 详见 `spec/06-商业化变现与合规避险.md` §6.1 业务红线

### 🚨 R2 - 金额精度

- Python 后端：金额一律用 `decimal.Decimal`，**禁止 float**
- TypeScript：金额一律用 `big.js`，**禁止 parseFloat**
- 数据库：金额字段一律 `NUMERIC(20,2)` 或 `NUMERIC(12,4)`

### 🚨 R3 - 引用源可追溯

- 任何 AI 生成的事实陈述必须挂引用 `[1][2]`
- 引用元数据必须存 `agent_messages.citations` JSONB 字段
- 详见 `spec/04-AI-Agent与数据源技术落地方案.md` §3.3

### 🚨 R4 - 改动控制

- 单次改动 > **300 行** 或 跨 **3 个以上不相关文件** → 停下来跟用户确认拆分
- 不要"顺手"修改非任务相关的代码（即使你觉得能优化）
- 删除/重命名公共 API 之前必须确认

### 🚨 R5 - 危险操作护栏

- 禁止：`rm -rf`、`git push --force` 到 main、`DROP TABLE/DATABASE`、`alembic downgrade base`
- 禁止：把 `.env`、密钥、Token 提交到 Git
- 任何破坏性操作必须先 Dry-Run 给用户看

---

## 3. 仓库结构索引

```
xgzh/
├── spec/         # 产品设计文档（不要随意改，改前确认）
├── apps/
│   ├── api/      # FastAPI 业务后端（Python 3.12 + uv）
│   ├── agent/    # FastAPI Agent 服务（RAG / Tool Use）
│   ├── mp/       # UniApp 客户端（小程序+App+H5）
│   └── admin/    # 运营后台（暂留）
├── packages/
│   ├── shared-types/  # 跨端共享类型（OpenAPI 自动生成）
│   ├── prompts/       # 版本化 Prompt
│   └── eval/          # AI 离线评测集
├── infra/        # docker-compose、helm、alembic
└── .cursor/      # rules + hooks（AI 护栏）
```

## 4. 工作流约定

- **接到任务先读 spec**：找到对应模块的设计章节再动手
- **Plan 模式**：架构选型、超过 1 文件的改动，先 Plan 后 Code
- **小步提交**：每个可工作的功能单独 commit，不要憋大 PR
- **先跑通后优化**：MVP 阶段优先"端到端能跑"，性能/优雅度后置

## 5. 启动命令速查

```bash
# 启动基础设施（PG/Redis/Meilisearch）
cd infra && docker compose up -d

# 启动后端
cd apps/api && uv sync && uv run uvicorn app.main:app --reload --port 8000

# 启动小程序（HBuilderX 打开 apps/mp 项目，或：）
cd apps/mp && pnpm install && pnpm dev:mp-weixin
```

---

> 🎯 **执行原则**：当你不确定时，**问，不要猜**。这个项目涉及金融合规，错一个词可能引发监管风险。
