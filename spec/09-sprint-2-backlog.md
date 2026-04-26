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
| BE-S2-000 | data | HK IPO ingest 真源接入（hkexnews 列表 / Futu OpenAPI 二选一）| 1d | — | P0 | ⬜ |
| BE-S2-001 | db | 会话/消息/工具调用/Token 4 张表 + ORM + Alembic 0002 | 1d | — | P0 | ✅ |
| BE-S2-002 | adapter | LLM facade 重构（chat/embedding/rerank 三入口 + multi-provider）| 0.5d | — | P0 | ✅ |
| BE-S2-003 | db | pgvector `document_chunks` 表 + HNSW + Alembic 0003 | 0.5d | BE-S2-001 | P0 | ⬜ |
| BE-S2-004 | rag | 招股书 PDF 解析 + 语义切分 + 批量 Embedding 入库 | 1d | BE-S2-000, BE-S2-002, BE-S2-003 | P0 | ⬜ |
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

- **16 PR**，约 **14 工作日**
- 后端 10 PR（~10d，含 BE-S2-000 HK ingest）+ 前端 4 PR（~2.5d）+ 测试 2 PR（~1.5d）

> Sprint 1.5 收尾包（已合并）：缓存失效 hook + Makefile DX + ✅ 关闭 Sprint 1 的 BE-008/009/QA-001 三处遗留。详见 `spec/08-sprint-1-backlog.md` §Sprint 1.5。

---

## 🔗 依赖图（mermaid 简版）

```
BE-S2-000 (HK ingest) ────────┐
                              │
BE-S2-001 (4 张表)            │       BE-S2-002 (LLM facade)
    │                         │           │
    ├──► BE-S2-003 (pgvector) │           │
    │       │                 │           │
    │       ├──► BE-S2-004 (招股书入库) ──┘
    │       └──► BE-S2-005 (混合检索) ────┐
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

**HK ingest 并行路径**：BE-S2-000 与 BE-S2-001 / BE-S2-002 / BE-S2-003 三者均无依赖，可并行起跑；只在 BE-S2-004（招股书 PDF 入库）时被强制汇合。

---

## 🎬 BE-S2-001 ✅ 已完成（2026-04-26）

### 实施成果

- 4 张表 + 6 个二级索引 + alembic 0002 全部落地
- `make test-all` **224 passed**（前 218 → 净增 6 条 BE-S2-001 集成测试）
- ruff 51 / mypy 24 = baseline 0 增量
- ORM models 与 spec 设计对齐 + 3 处工程化偏移已记录（见下文 §"实施偏差"）

### 实际改动文件

```
apps/api/alembic/versions/0002_add_chat_tables.py     # 新建（手写, up + downgrade）
apps/api/app/db/models/chat.py                        # 新建（4 个 ORM 合一文件, 而非 spec 写的拆 4 个文件）
apps/api/app/db/models/__init__.py                    # +export ChatSession/ChatMessage/ChatToolCall/ChatTokenUsage
apps/api/tests/integration/conftest.py                # truncate_all 列表 +chat_sessions
apps/api/tests/integration/test_chat_tables.py        # 新建（6 条用例）
```

### 实施偏差（vs spec/09 原稿）

1. **ORM 拆文件 → 单文件 `chat.py`**：4 个 model 共享设计上下文（命名 / 级联 / 索引），单文件 ~280 行可读；与 Sprint 1 `ipo.py` 装下 IPO + IPODocument 的风格一致
2. **PK 命名 `<entity>_id` 而非 spec 写的 `id`**：沿 Sprint 1 风格（`session_id` / `message_id` / `tool_call_id` / `usage_id`），保持代码搜索 / SQL JOIN 时一眼能认出 PK
3. **`chat_messages.tool_call_id` → `openai_tool_call_id`**：避免与 `chat_tool_calls.tool_call_id` (UUID PK) 同名歧义；前缀 `openai_` 明示这是协议字符串而非 FK
4. **3 个枚举字段一律 `String + comment`，不用 PG ENUM**：与 Sprint 1 `ipos.status` 同方案，加值不需要 `ALTER TYPE`
5. **`chat_sessions.user_id` `ON DELETE SET NULL`**：与 invite_codes.owner_user_id 同策略, 用户注销后会话变匿名而非物理删（保留运营数据）
6. **`chat_messages` / `chat_tool_calls` / `chat_token_usage` 不带 TimestampMixin**：写入即历史, 只留 `created_at`, 防 LLM 输出落库后被改写

### 下一步推荐

| 候选 | 理由 |
|------|------|
| **BE-S2-002** (LLM facade, 0.5d) | 无依赖 + 短 PR + 后续 BE-S2-004/007 会重度依赖；先把 chat/embedding/rerank 三入口架好 |
| BE-S2-000 (HK ingest, 1d) | 也无依赖，但偏慢（要写 hkexnews adapter + scheduler 改），BE-S2-004 才会真用上 |
| BE-S2-006a (Tool 注册中心, 1d) | 已可起，但用 LLM facade 之前不能完全调通；BE-S2-002 完了再做更顺 |

→ **建议下一步走 BE-S2-002**（短平快 + 解锁后续 BE-S2-004/006a/007 三条线）

---

## 🎬 BE-S2-002 ✅ 已完成（2026-04-26）

### 实施成果

- 单文件 `app/adapters/llm_client.py` 内重构 + 加 `chat / embed / rerank` 三入口
- 新增 5 个 frozen dataclass: `TokenUsage / ChatResult / ChatStreamChunk / EmbeddingResult / RerankResult`
- 新增 3 层异常: `LLMError(基类) → LLMConfigError / LLMProviderError`，端层 main.py handler 可统一映射 503/502
- `make test-all` **248 passed**（前 224 → **净增 24** 条 BE-S2-002 facade 单测）
- ruff **51 = baseline 0 增量**，mypy **23（baseline 24, 顺手清掉一处过期 type: ignore, −1 改善）**
- Sprint 1 老调用方 4 处 (`agent_service` / `test_compliance` / `tests/integration/conftest.py:fake_llm` / `apps/api/app/adapters/__init__.py`) 全部向后兼容，无任何修改

### 实际改动文件

```
apps/api/app/core/config.py                    # +llm_embedding_model/dim/batch_size/llm_rerank_model/llm_chat_default_temperature/llm_request_timeout_seconds
apps/api/.env.example                          # +Embedding/Rerank 段
apps/api/app/adapters/llm_client.py            # 重写: 137 行 → 488 行
apps/api/tests/test_llm_facade.py              # 新建（24 条单测，全 mock 不打远程 LLM）
```

### 三入口契约

| 入口 | 用途 | 关键返回字段 |
|------|------|-------------|
| `chat()` | 非流式（LangGraph 决策步：要拿 tool_calls） | `ChatResult{ content, tool_calls, finish_reason, usage }` |
| `stream_chat()` | 老 SSE 兼容（yield str + 末尾 disclaimer） | str token |
| `astream_chat_with_meta()` | 新流式（BE-S2-007 主循环用：要拿 usage / tool_calls） | `ChatStreamChunk{ delta? \| (finish_reason, usage, tool_calls) }` |
| `embed(texts)` | 批量嵌入（自动按 32 分批） | `EmbeddingResult{ embeddings: [[float;1024]], usage }` |
| `rerank(query, docs, top_n)` | 候选文档重排（直接 httpx → 硅基流动 /v1/rerank cohere 兼容协议） | `RerankResult{ results: [(orig_idx, score)] desc, usage }` |

### Provider 路由（按 model 字符串前缀 dispatch）

- `openai/...` → 硅基流动 OpenAI 兼容（chat / embedding 通用）
- `deepseek/...` → DeepSeek 官方
- `zhipu/...` → 智谱
- 未匹配 → `LLMConfigError` 抛端层

### 实施偏差（vs spec/09 原稿）

1. **不拆包 → 单文件 488 行**：spec 没硬要求拆，单文件用 region 区分（合规护栏 / 数据类 / Provider 路由 / 三入口）保持 `from app.adapters import llm_client` import 路径稳定，不级联改 4 处老调用方
2. **流式 chat 拆两个 API**（`stream_chat` 老 + `astream_chat_with_meta` 新）：原 `stream_chat` yield str 的契约在 e2e SSE 测试里被 deeply 依赖，破坏成本高；新增 `astream_chat_with_meta` 给 BE-S2-007 用，老入口标记 deprecated 但不删
3. **rerank 不走 LiteLLM**：LiteLLM 1.51 的 `arerank` 路由只走 cohere 官方（要 `COHERE_API_KEY` env），用于硅基流动需要 hack base_url；直接 `httpx.AsyncClient` POST `/v1/rerank` 反而更直接，cohere 协议兼容
4. **成本表 hardcode**：`_PRICE_CNY_PER_M_TOKENS` 内置 8 条价格条目（DeepSeek-V3 / V2.5 / glm-4-flash / bge-m3 / bge-reranker 等），未匹配 fallback 到 `Decimal('0')` + warn（不抛），防 `chat_token_usage.cost_cny` NOT NULL 触发；Sprint 3+ 真做成本看板再做配置化
5. **embed 数量校验**：响应向量数 ≠ 输入文本数时抛 `LLMProviderError`，防 BE-S2-004 招股书 chunk 与 embedding 错位入库（沉默故障会污染整个 RAG 索引）

### 下一步推荐

| 候选 | 理由 |
|------|------|
| **BE-S2-003** (pgvector + Alembic 0003, 0.5d) | 短 PR + 解锁 BE-S2-004 + BE-S2-005；和 BE-S2-001/002 同属"基建一刀切" |
| BE-S2-006a (Tool 注册中心, 1d) | 已可起：BE-S2-001 chat 表 + BE-S2-002 facade 都齐；但要 7 维 IPO 数据 schema 对齐, 写起来略慢 |
| BE-S2-000 (HK ingest, 1d) | 也无依赖，但 BE-S2-004 招股书入库才会真用上, 可挪后 |

→ **建议下一步走 BE-S2-003**（先把向量检索基建落，BE-S2-004/005 后续两条 RAG 路全打开）

---

### BE-S2-001 详细规格（落地版）

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

**AC（已全部勾选）**

- [x] 4 张表迁移 `alembic upgrade head` 成功，`alembic downgrade -1` 干净（迁移可回滚）— `test_alembic_downgrade_then_upgrade_idempotent`
- [x] ORM models 全部定义 + `__init__.py` re-export，`SQLAlchemy 2.0 DeclarativeBase` 风格
- [x] 外键级联验证：用户删 → session.user_id SET NULL（`test_chat_session_user_id_set_null_on_user_delete`）；session 删 → messages / tool_calls / token_usage 全 CASCADE（`test_chat_messages_cascade_delete_on_session_delete`）
- [x] 索引验证：`pg_indexes` 查到 6 个二级索引齐（`test_migration_creates_4_tables_with_all_indexes`）
- [x] 单测 6 条（≥ 4）：schema 形状 + 迁移幂等 + 双向级联 + 完整链路 INSERT/SELECT + append-only 守护
- [x] mypy 0 增量（baseline 24 维持）
- [x] ruff 0 增量（baseline 51 维持）
- [x] 顶层 README + spec/09 + spec/08 同步

**关键设计决策（提前定）**

1. **`user_id` 可空**：spec/04 §1.3 设计为"匿名也能用 AI"。匿名会话先存，登录后绑定 `user_id`（Sprint 2 不做绑定，先存 null）
2. **`citations` 用 jsonb 而不是单独表**：列表平均 5 项，jsonb 写读简单；后续如要做"被引用最多的招股书段落"统计再拆 `chat_message_citations` 表
3. **`cost_cny` 用 `numeric(10,6)`**：单次成本 ~¥0.005-0.05，6 位小数足够；按累计聚合用 `sum(cost_cny)::numeric(20,6)`
4. **不存 prompt 全文**：`chat_messages.content` 只存"用户可见消息"，system prompt 不存表（spec/04 §五-1 Prompt 版本化由 git CHANGELOG 管，不入业务表，避免每条消息冗余几 KB）
5. **`tool_call_id` 不做外键**：用 OpenAI Tool Use 的 string id 形式，是 `chat_tool_calls.id` 之外的另一个表达；正式 FK 在 `chat_tool_calls.message_id` 上

---

## 📦 BE-S2-000 详细规格（HK IPO ingest 真源 — 与 BE-S2-001 并行可起）

### 为什么单独立项

Sprint 1 的 BE-007 周期任务只接了 A 股（AKShare 1.18 没干净的 HK IPO API），HK 一直走内存 seed 的 3 条样例。但 Sprint 2 的 RAG 主战场是**招股书 PDF**：

- A 股招股书在证监会网站，反爬很狠 + 单 PDF 几十 MB + 经常被改版（且监管下载有合规争议）
- HK 招股书在 [hkexnews.hk](https://www.hkexnews.hk) 完全公开，按上市编号一一对应（A1 / A1A 表），格式相对稳定（PDF + 中英双版本）
- spec/04 §4 已写 "HK 是 RAG MVP 主战场"，没真 IPO 列表 → 没 PDF URL → BE-S2-004 无米下锅

所以 HK ingest 必须放到 BE-S2-004 之前，不能拖到 Sprint 3。

### 改动文件

```
apps/api/app/adapters/hkex_client.py                  # 新建（hkexnews 列表 + 招股书 URL 抓取）
apps/api/app/services/ipo_ingest_service.py           # 新增 run_ingest_hk_job
apps/api/app/scheduler/__init__.py                    # 注册 hk cron job
apps/api/app/core/config.py                           # +ipo_ingest_hk_limit / hkex_base_url
apps/api/.env.example                                 # +HKEX_BASE_URL placeholder
apps/api/tests/test_hkex_client.py                    # 新建（respx mock 列表 + PDF URL）
apps/api/tests/test_ipo_ingest.py                     # +HK 路径 happy / 网络失败 / 空结果 3 条
```

### 数据源选择（二选一，PR 内拍板）

| 选项 | 优势 | 劣势 |
|------|------|------|
| **hkexnews 公开列表（推荐）** | 完全公开，无需 API key，PDF URL 在同一页 | HTML 解析（BeautifulSoup），需要 IP 友好（不要太频繁，2 req/s 上限） |
| Futu OpenAPI | 结构化 JSON，字段全（含一手中签率历史） | 需要 token 注册 + 调用配额；商业用途要核合规 |

**推荐 hkexnews + httpx**：MVP 阶段不引入额外凭据；BeautifulSoup 已是 BE-007 间接依赖（akshare 用），不增包体。Sprint 3+ 真上线规模上去了再切 Futu。

### AC

- [ ] `fetch_hk_ipos(limit: int = 100) -> list[IPOItem]`：从 hkexnews "近期上市 / 招股中" 列表抓取，含 `code` (5 位港股代码 `0700.HK`) / `name` / `market="HK"` / `subscribe_start` / `subscribe_end` / `listing_date` / `prospectus_url`（招股书 PDF URL，存进 `extra.prospectus_url`）
- [ ] 失败兜底：网络 5xx / HTML 改版 / 解析失败时 `logger.warning` + 返回空 list，不 raise（与 BE-007 A 股路径一致）
- [ ] 速率限制：`asyncio.Semaphore(2)` 限并发 + `httpx.AsyncClient(timeout=10)` 防长 hang
- [ ] `run_ingest_hk_job()`：调 `fetch_hk_ipos` → `upsert_ipos`（复用 BE-007 写入逻辑）→ `invalidate_namespace("ipos:list", "ipos:detail")`（复用 Sprint 1.5 收尾包的 hook）
- [ ] APScheduler 每天跑一次（HK IPO 节奏比 A 股慢，cron `30 9 * * *` 早 9:30 一波 + `30 16 * * *` 收盘后一波二刀流即可）
- [ ] `ipo_service.list_ipos(market="HK")` 切回 DB 路径（拆掉内存 seed 分支），保留 seed 作为 init fallback（DB 空时启动一次 ingest 不阻塞）
- [ ] 单测 ≥ 5 条：respx mock hkexnews HTML（happy / 列表为空 / HTTP 5xx / 解析失败 / `run_ingest_hk_job` happy + cache 失效一起）

### 关键设计决策（提前定）

1. **`code` 格式统一为 `XXXXX.HK`（5 位补零）**：与 Sprint 1 favorites store / FE-005 详情页一致，不引入新格式（hkexnews 原始展示是无后缀的 `0700`，adapter 层补 `.HK`）
2. **`prospectus_url` 存进 `extra` 而非顶层列**：BE-009 `IPODetail.prospectus_url` 已从 `extra` 读，走同一存储格式不动 schema
3. **不在本 PR 做招股书 PDF 下载**：那是 BE-S2-004 的职责（要做切分 + embedding）；BE-S2-000 只负责把 PDF URL 入库，文件下载不在范围内
4. **HK 与 A 股共享同一份 cron 注册函数**：`scheduler/register_jobs` 内部按 `if settings.scheduler_enabled: register a + register hk` 判断，单 pod 配置即开
5. **保留 seed 作为 cold-start fallback**：DB 空表（lifespan startup 第一次跑）时 `ipo_service.list_ipos(market="HK")` 还能拿到 3 条样例，不让首次部署的用户看到空首页
6. **复用 Sprint 1.5 的 cache invalidation**：`run_ingest_hk_job` 末尾 `await invalidate_namespace("ipos:list", "ipos:detail")` —— 这就是 Sprint 1.5 收尾包给 Sprint 2 留下的复用价值，HK 写完 ipos 表后立即让缓存回源，前端 / RAG 都看到最新

### 不做（明确 P1+）

- HK 一手中签率历史（hkexnews 没这字段，要爬 [aastocks](https://www.aastocks.com) 或 [moomoo](https://moomoo.com) → 反爬 + 数据合规风险，Sprint 3 接 Futu 时一起做）
- 暗盘行情 / 灰盘价（券商数据，需付费，Sprint 4 VIP 增值功能）
- 港股新股辅助计算器（认购倍数 / 中签预估，Sprint 3 业务功能）

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
- HK IPO 走真源 ingest（hkexnews）入库，招股书 URL 闭环到 RAG 流水线
- 16 PR + 累计 ≥ 250 个测试 + 11 张 DB 表 + 1 个 LangGraph + 80 条评测集

> 然后进入 Sprint 3（文章聚合 + 券商对比 + VIP 订阅），spec/07 §S3 拆任务时再开新 backlog 文档。
