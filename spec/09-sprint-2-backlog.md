# 09 - Sprint 2 Backlog: AI Agent + RAG（核心壁垒）

> Sprint 1 ✅ 11 BE + 6 FE + 1 QA = 18 PR 已合，211 个测试 + 7 张表 + 1 调度任务
>
> Sprint 2 主战场（spec/07 §Sprint 2）：
> 1. **招股书向量检索**（pgvector + bge-m3 + bge-reranker-v2 + 招股书 PDF 解析）
> 2. **LangGraph Tool Use**（5 个工具：基本面 / 财务 / 同业 / 情感 / 历史）
> 3. **Agent 主循环 + 引用源装配 + 合规端层兜底 disclaimer**
> 4. **配额管理 + 评测集 80 条 + 前端对话页 UI + 打字机渲染**
>
> 排期：约 13 工作日（15 PR），按 vibe coding 单人节奏 ≈ 2-2.5 周。spec/07 §S2 估 18 BE + 9 FE + 19 AI ≈ 46 人天对应 5 人团队 1.5 周，对单人节奏合理。

---

## 🎯 Sprint 2 Scope Lock

### ✅ 必做（P0）

| 模块 | 必做范围 |
|------|---------|
| 1. LLM facade | LiteLLM 升级，`chat / embedding / rerank` 三入口，多 provider 切换，硅基流动 / DeepSeek / 智谱 |
| 2. 会话记账 | 4 张表（`chat_sessions` / `chat_messages` / `chat_tool_calls` / `chat_token_usage`）+ token / cost 全量记账 |
| 3. RAG 流水线 | pgvector + `document_chunks` 表 + 招股书 PDF 解析 + 切分 + 入库 |
| 4. 检索 | 混合检索（向量 + BM25）+ RRF 融合 + bge-reranker top5 |
| 5. Tool Use | 5 个工具实现 + 注册中心 + JSON schema 描述 + 沙盒错误兜底 |
| 6. Agent 主循环 | LangGraph 状态机 ReAct loop（max 5 步）+ 引用源装配 + 合规端层 disclaimer |
| 7. 配额 | 免费 5 次/天 / VIP 无限 / 滑动窗口 / 友好提示 |
| 8. 评测集 | 80 条标注 query + 离线评测脚手架（召回@5 / 幻觉率 / LLM-as-judge）|
| 9. 前端对话页 | 消息列表 + 推荐问题 chips + 打字机渲染 + 引用面板 + 配额引导 |
| 10. e2e 测试 | Agent SSE 全链路（含工具调用 + 引用源 + 限流 + 兜底）|

### 🟡 后置（P1，Sprint 3 再做）

- 多模型路由策略（按意图分类调度 Doubao-Lite / GLM-Flash / DeepSeek-V3 / DeepSeek-R1）—— Sprint 2 只接 1 个主力（DeepSeek-V3）+ 1 个降级（GLM-4-Flash），路由策略后置
- HyDE 检索增强 —— 第一刀直接 vec + BM25 RRF，HyDE 等评测集 baseline 出来再加
- Prompt 版本化 + Feature Flag 灰度 —— 走最简 git diff 复盘，Sprint 3+ 加版本化
- 在线 Bad Case 闭环（用户给"踩"反馈进运营后台）—— 后端预留 `chat_message.feedback` 字段，UI 不做
- 文章情感打标实数据 —— Sprint 2 占位 `tool_get_news_sentiment` 返回 mock，Sprint 3 文章流水线接好后切真数据

### ❌ 不做

- 多模态（图片 / 表格识别）
- 客户端 LLM 推理（边端模型）
- WebSocket 全双工 —— 继续用 SSE
- 复杂 Tool Chain（工具间数据依赖图）—— ReAct 单跳就够 MVP

---

## 📦 任务面板（按依赖排）

> 单 PR 粒度延续 Sprint 1 节奏：0.5d ~ 1.5d。每张卡都带 AC + 改动文件 + 依赖。

### 后端 · BE-S2

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| BE-S2-001 | db | 会话/消息/工具调用/Token 4 张表 + ORM + Alembic 0002 | 1d | — | P0 | ⬜ |
| BE-S2-002 | adapter | LLM facade 重构（chat/embedding/rerank 三入口 + multi-provider）| 0.5d | — | P0 | ⬜ |
| BE-S2-003 | db | pgvector `document_chunks` 表 + HNSW + Alembic 0003 | 0.5d | BE-S2-001 | P0 | ⬜ |
| BE-S2-004 | rag | 招股书 PDF 解析 + 语义切分 + 批量 Embedding 入库 | 1d | BE-S2-002, BE-S2-003 | P0 | ⬜ |
| BE-S2-005 | rag | 混合检索 + RRF 融合 + bge-reranker 重排 | 1d | BE-S2-003 | P0 | ⬜ |
| BE-S2-006a | agent | Tool 注册中心 + 2 个最简工具（basic_info / financial）| 1d | BE-S2-001 | P0 | ⬜ |
| BE-S2-006b | agent | 余下 3 个 Tool（peers / sentiment_placeholder / historical）+ 沙盒 | 1d | BE-S2-006a | P0 | ⬜ |
| BE-S2-007 | agent | LangGraph 主循环 + 引用源装配 + 合规端层 disclaimer | 1.5d | BE-S2-002, BE-S2-005, BE-S2-006b | P0 | ⬜ |
| BE-S2-008 | quota | Agent 配额管理（免费 5/天 + VIP 无限 + 滑动窗口）| 0.5d | BE-S2-007 | P0 | ⬜ |
| BE-S2-009 | eval | 评测集 80 条 + 离线评测脚手架（召回@5 / 幻觉率 / LLM-as-judge）| 1d | BE-S2-007 | P0 | ⬜ |

### 前端 · FE-S2

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| FE-S2-001 | page | AI 对话页 UI + Pinia store + SSE consume | 1d | BE-S2-007 | P0 | ⬜ |
| FE-S2-002 | render | 打字机渲染 + Markdown 增量解析 + MP-WEIXIN onChunkReceived 兼容 | 0.5d | FE-S2-001 | P0 | ⬜ |
| FE-S2-003 | render | 引用源面板（[1] 点击 → ActionSheet → 原文片段抽屉）| 0.5d | FE-S2-001 | P0 | ⬜ |
| FE-S2-004 | quota | 配额限制 UI + VIP 升级引导 modal（接 429）| 0.5d | BE-S2-008, FE-S2-001 | P0 | ⬜ |

### 测试 · QA-S2

| ID | 类别 | 标题 | 估时 | 依赖 | 优先级 | 状态 |
|----|------|------|:----:|:----:|:------:|:----:|
| QA-S2-001 | e2e | Agent E2E 集成测试（LangGraph + tool 调用 + 引用源 + 限流 + 兜底）| 1d | BE-S2-007, BE-S2-008 | P0 | ⬜ |
| QA-S2-002 | eval | RAG 评测集 CI 化（`make eval-sprint2` 跑分 + 阈值告警）| 0.5d | BE-S2-009 | P0 | ⬜ |

### 总计

- **15 PR**，约 **13 工作日**
- 后端 9 PR（~9d）+ 前端 4 PR（~2.5d）+ 测试 2 PR（~1.5d）

---

## 🔗 依赖图（mermaid 简版）

```
BE-S2-001 (4 张表)         BE-S2-002 (LLM facade)
    │                          │
    ├──► BE-S2-003 (pgvector)  │
    │       │                  │
    │       ├──► BE-S2-004 (招股书入库) ──┐
    │       └──► BE-S2-005 (混合检索) ────┤
    │                                     ▼
    └──► BE-S2-006a (basic_info, financial)
              │
              └──► BE-S2-006b (peers, sentiment, historical)
                       │
                       └──► BE-S2-007 (LangGraph 主循环) ────┐
                                  │                          │
                                  ├──► BE-S2-008 (配额) ──┐  │
                                  │                       │  │
                                  └──► BE-S2-009 (评测集) │  │
                                                          ▼  ▼
                                                    QA-S2-001 (e2e)
                                                    QA-S2-002 (eval CI)
                                                          ▲
                                                          │
                              FE-S2-001 (对话页) ──┬──────┘
                                  │                │
                                  ├── FE-S2-002 (打字机)
                                  ├── FE-S2-003 (引用面板)
                                  └── FE-S2-004 (配额 UI)
```

**关键路径**：BE-S2-001 → BE-S2-006a → BE-S2-006b → BE-S2-007 → FE-S2-001 → QA-S2-001（约 7 天串行）

---

## 🎬 推荐起跳：**BE-S2-001**

### 为什么 BE-S2-001 先做

1. **底座价值**：后续 Agent / 工具调用 / 评测集 / 成本看板**全部**依赖会话记账表，这条链是 critical path
2. **纯 schema 题**：没有业务逻辑，0 风险一刀切，节奏快
3. **`alembic 0002` 占位**：和 `BE-S2-003 alembic 0003` 是同一波 migration，先把版本号占住
4. **测试零成本**：复用 Sprint 1 已有的 `tests/integration/conftest.py` schema reset 逻辑

### BE-S2-001 详细规格

**改动文件**

```
apps/api/alembic/versions/0002_add_chat_tables.py     # 新建
apps/api/app/db/models/chat_session.py                # 新建
apps/api/app/db/models/chat_message.py                # 新建
apps/api/app/db/models/chat_tool_call.py              # 新建
apps/api/app/db/models/chat_token_usage.py            # 新建
apps/api/app/db/models/__init__.py                    # export 新模型
apps/api/tests/test_chat_tables.py                    # 新建（schema 形状 + 外键 + 索引验证）
```

**Schema 设计**

```python
# chat_sessions: 一条用户对话会话
chat_sessions:
  - id: UUID PK
  - user_id: UUID FK -> users (nullable, 支持匿名诊断后绑定)
  - ipo_code: str(16) nullable index  # 会话锚定的新股
  - title: str(64)                     # 自动从首问生成 / 手动改
  - status: enum('active' | 'archived' | 'deleted')
  - created_at / updated_at: timestamptz
  - INDEX (user_id, created_at DESC)   # 用户最近会话
  - INDEX (ipo_code, created_at DESC)  # 某 IPO 的最近讨论

# chat_messages: 会话内消息
chat_messages:
  - id: UUID PK
  - session_id: UUID FK -> chat_sessions ON DELETE CASCADE
  - role: enum('user' | 'assistant' | 'tool' | 'system')
  - content: text                      # markdown
  - tool_call_id: str(64) nullable     # role='tool' 时关联
  - citations: jsonb nullable          # [{idx:1, doc_id, chunk_id, source_url}, ...]
  - feedback: smallint nullable        # 用户 +1 / -1 / null（Sprint 3 反馈闭环）
  - created_at: timestamptz
  - INDEX (session_id, created_at)

# chat_tool_calls: Tool Use 调用记录
chat_tool_calls:
  - id: UUID PK
  - message_id: UUID FK -> chat_messages ON DELETE CASCADE
  - tool_name: str(64)
  - args: jsonb
  - result: jsonb nullable             # 失败时 null
  - status: enum('pending' | 'ok' | 'error' | 'timeout')
  - error_message: text nullable
  - latency_ms: int nullable
  - created_at: timestamptz
  - INDEX (tool_name, created_at)      # 工具用量统计

# chat_token_usage: token / cost 记账（成本看板基础）
chat_token_usage:
  - id: UUID PK
  - message_id: UUID FK -> chat_messages ON DELETE CASCADE
  - model: str(64)                     # 'deepseek-v3' / 'glm-4-flash'
  - input_tokens: int
  - output_tokens: int
  - cost_cny: numeric(10, 6)           # ¥, 6 位小数
  - provider: str(32)                  # 'siliconflow' / 'deepseek' / 'zhipu'
  - created_at: timestamptz
  - INDEX (model, created_at)          # 按模型统计
  - INDEX (created_at)                 # 时间序列报表
```

**AC**

- [ ] 4 张表迁移 `alembic upgrade head` 成功，`alembic downgrade -1` 干净（迁移可回滚）
- [ ] ORM models 全部定义 + `__init__.py` re-export，`SQLAlchemy 2.0 DeclarativeBase` 风格
- [ ] 外键 ON DELETE CASCADE 验证：删除 session → messages 全部清除；删除 message → tool_calls / token_usage 跟着清
- [ ] 索引验证：`pg_indexes` 查到 6 个二级索引（`(user_id, created_at)` / `(ipo_code, created_at)` / `(session_id, created_at)` / `(tool_name, created_at)` / `(model, created_at)` / `(created_at)`）
- [ ] 单测 ≥ 4 条：建会话 / 加消息 / 关联 tool call / 累计 token usage（用 `tests/integration/conftest.py` 的 `session_factory`）
- [ ] `uv run mypy app/db/models/` 0 报错
- [ ] `uv run ruff check` 0 报错
- [ ] 顶层 README + spec/09 标 ✅

**关键设计决策（提前定）**

1. **`user_id` 可空**：spec/04 §1.3 设计为"匿名也能用 AI"。匿名会话先存，登录后绑定 `user_id`（Sprint 2 不做绑定，先存 null）
2. **`citations` 用 jsonb 而不是单独表**：列表平均 5 项，jsonb 写读简单；后续如要做"被引用最多的招股书段落"统计再拆 `chat_message_citations` 表
3. **`cost_cny` 用 `numeric(10,6)`**：单次成本 ~¥0.005-0.05，6 位小数足够；按累计聚合用 `sum(cost_cny)::numeric(20,6)`
4. **不存 prompt 全文**：`chat_messages.content` 只存"用户可见消息"，system prompt 不存表（spec/04 §五-1 Prompt 版本化由 git CHANGELOG 管，不入业务表，避免每条消息冗余几 KB）
5. **`tool_call_id` 不做外键**：用 OpenAI Tool Use 的 string id 形式，是 `chat_tool_calls.id` 之外的另一个表达；正式 FK 在 `chat_tool_calls.message_id` 上

---

## 🛡 Sprint 2 不能碰的事

- 真实 LLM Key 写到代码里（永远走 `.env` + `fake_llm` fixture mock）
- 招股书全文存到 `chat_messages.content`（侵犯版权 + 表暴涨）→ 只存 chunk reference
- 删除 Sprint 1 的 `agent_service.diagnose_stream` 单 shot 路径（保留作为 Tool Use 失败兜底，至少留到 Sprint 3 才砍）
- 在 `agent.py` 路由层加 `get_current_user` 强制鉴权（spec/04 §1.3 允许匿名；要改先改 spec）
- 任何 `pg_dump` / 真实生产数据进 `evals/` 目录（评测集必须是合成 / 公开 / 已脱敏数据）

## ✅ Sprint 2 完成后的产出物

- 用户在对话页可以多轮追问任意一只新股（招股书 RAG 引用源可点开看）
- AI 输出在硬护栏（关键词过滤 + 强制免责声明 + 引用强制校验）下基本不出违规
- 评测集 80 条 → 召回@5 ≥ 0.7（baseline）/ 幻觉率 ≤ 10% / 单次平均成本 < ¥0.05
- 配额 5 次/天 / VIP 无限的限流闭环跑通，前端有友好升级引导
- 15 PR + 累计 ≥ 240 个测试 + 11 张 DB 表 + 1 个 LangGraph + 80 条评测集

> 然后进入 Sprint 3（文章聚合 + 券商对比 + VIP 订阅），spec/07 §S3 拆任务时再开新 backlog 文档。
