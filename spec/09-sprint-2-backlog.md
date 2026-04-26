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
| BE-S2-000 | data | HK IPO ingest 真源接入（hkexnews 列表 / Futu OpenAPI 二选一）| 1d | — | P0 | ✅ |
| BE-S2-001 | db | 会话/消息/工具调用/Token 4 张表 + ORM + Alembic 0002 | 1d | — | P0 | ✅ |
| BE-S2-002 | adapter | LLM facade 重构（chat/embedding/rerank 三入口 + multi-provider）| 0.5d | — | P0 | ✅ |
| BE-S2-003 | db | pgvector `ipo_documents` 扩展 + 防重 + Alembic 0003 | 0.5d | BE-S2-001 | P0 | ✅ |
| BE-S2-004 | rag | 招股书 PDF 解析 + 语义切分 + 批量 Embedding 入库 | 1d | BE-S2-000, BE-S2-002, BE-S2-003 | P0 | ✅ |
| BE-S2-005 | rag | 混合检索 + RRF 融合 + bge-reranker 重排 | 1d | BE-S2-003 | P0 | ✅ |
| BE-S2-006a | agent | Tool 注册中心 + 2 个最简工具（basic_info / financial）| 1d | BE-S2-001 | P0 | ✅ |
| BE-S2-006b | agent | 余下 3 个 Tool（peers / sentiment_placeholder / historical）+ hybrid_search Tool 化 | 1d | BE-S2-006a | P0 | ✅ |
| BE-S2-007 | agent | LangGraph 主循环 + 引用源装配 + 合规端层 disclaimer | 1.5d | BE-S2-002, BE-S2-005, BE-S2-006b | P0 | ✅ |
| BE-S2-008 | quota | Agent 配额管理（免费 5/天 + VIP 无限 + 滑动窗口）| 0.5d | BE-S2-007 | P0 | ✅ |
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

## BE-S2-003 实施成果（2026-04-26 落地）

### 关键决策（vs spec 原稿）

1. **不新建 `document_chunks` 表，而是 ALTER 已有 `ipo_documents`**
   - `ipo_documents` 在 0001_init 已建（`vector(1024)` + HNSW cosine + `ipo_code/doc_type/doc_id` 索引），但 Sprint 1 零写入流量
   - 改名为 `document_chunks` 要级联改 ORM / 索引名 / `test_migrations.py` / spec/05 / spec/04 多处，diff 大且收益不明（"ipo_documents" 也是 RAG chunks，命名一致 Sprint 1 风格）
   - 0003 走 `ALTER TABLE ipo_documents ADD COLUMN ...`，老 schema 零变动
2. **全文检索 / `tsvector` 列 punt 给 BE-S2-005（一条独立 0004 migration）**
   - 中文分词器选型本身是独立决策（zhparser 装难、pg_trgm 简单只能模糊匹配、应用层 rank-bm25 便宜可控），BE-S2-005 真做混合检索代码时再敲定
   - 本 PR 不替 BE-S2-005 做选型；只做"BE-S2-004 入库流水线必需"的最小集

### 改动文件

```
apps/api/alembic/versions/0003_extend_ipo_documents.py     # 新建（ALTER 6 列 + 2 索引）
apps/api/app/db/models/ipo.py                              # IPODocument 同步加 6 字段 + 2 Index 声明
apps/api/tests/test_migrations.py                          # EXPECTED_INDEXES_SUBSET 加 2 个新名
apps/api/tests/integration/conftest.py                     # truncate_all 加 ipo_documents
apps/api/tests/integration/test_document_chunks_schema.py  # 新建 8 条集成用例
apps/api/tests/integration/test_chat_tables.py             # downgrade -1 → downgrade 0001_init（破除单 sprint 假设）
```

### Schema 增量

| 列 | 类型 | 默认 / Nullable | 用途 |
|------|------|---------|------|
| `chunk_index` | INTEGER | NULL | 同 doc 内顺序号（取上下文 ±1 / 排序） |
| `token_count` | INTEGER | NULL | bge-m3 tokenizer token 数（cost 调试） |
| `content_hash` | CHAR(64) | NULL | sha256(text) 16 进制；防同一 chunk 反复入库 |
| `embedding_model` | VARCHAR(64) | NOT NULL DEFAULT `'BAAI/bge-m3'` | 多版本向量共存 |
| `embedding_dim` | INTEGER | NOT NULL DEFAULT `1024` | 拒识维度不匹配的索引污染 |
| `lang` | VARCHAR(8) | NOT NULL DEFAULT `'zh'` | HK 招股书 `'en'` / A 股 `'zh'` |

| 索引 | 类型 | 用途 |
|------|------|------|
| `uq_ipo_documents_doc_id_content_hash` | UNIQUE PARTIAL `WHERE content_hash IS NOT NULL` | BE-S2-004 直接 `ON CONFLICT (doc_id, content_hash) DO NOTHING` 防重 |
| `ix_ipo_documents_doc_id_chunk_index` | PARTIAL `WHERE chunk_index IS NOT NULL` | 取相邻 chunk 上下文 / 拼回原文 |

### 测试矩阵（8 条集成用例 / 全 PG 真跑）

1. **schema 形状**：`information_schema.columns` 验 6 个新列 type / nullable / server_default
2. **partial UNIQUE 防重**：同 (doc_id, h1) 第二次插入抛 `IntegrityError`；不同 hash 或不同 doc 共存 OK
3. **NULL content_hash 共存**：3 条 `content_hash IS NULL` 的兼容老 Sprint 1 行可入库
4. **chunk_index 顺序还原**：乱序写入 5 个 chunk，`ORDER BY chunk_index ASC` 取回 [0,1,2,3,4]
5. **vector(1024) 实写实查**：5 个单位向量 + `embedding <=> CAST(:q AS vector)` cosine ANN，self 距离 < 1e-5（测 BE-S2-005 检索原语可用）
6. **NOT NULL DEFAULT 列填充**：`embedding_model/dim/lang` 不传也能写
7. **lang 显式覆盖**：`lang='en'` 给 HK 英文招股书
8. **0003 downgrade idempotent**：downgrade -1 后 6 列 + 2 索引消失、Sprint 1 老列 + HNSW 索引零损；upgrade head 后又全部回来

### 关键回归修复

`test_alembic_downgrade_then_upgrade_idempotent` 原硬编码 `command.downgrade(cfg, "-1")` 假设 head=0002；0003 落地后 `-1` 只回 0002（chat 表还在）。改为显式 `downgrade(cfg, "0001_init")`，破除单 sprint 假设，未来 0004/0005 加进来也不需再改。

### 测试基线

| 项目 | 旧 baseline (BE-S2-002 后) | 新 baseline (BE-S2-003 后) |
|------|------|------|
| pytest | 248 passed | **256 passed** (+8) |
| ruff | 51 errors | 51 errors（持平）|
| mypy | 23 errors | 23 errors（持平）|

### 下一步推荐

| 候选 | 理由 |
|------|------|
| **BE-S2-007** (LangGraph 主循环 + 引用源装配 + 端层 disclaimer, 1.5d) | 三依赖齐: BE-S2-002 facade ✅ + BE-S2-005 hybrid_search ✅ + BE-S2-006a Tool 注册中心 ✅ + BE-S2-006b 5 Tool ✅；现在 6 个 Tool 全部注册到位 (basic_info / financial / peers / sentiment_placeholder / historical / hybrid_search), ReAct 主循环可以起了 |
| BE-S2-008 (Agent 配额管理, 0.5d) | 要等 BE-S2-007，主循环写入 chat_messages 之后才有"成功调用 1 次"的口径 |
| BE-S2-009 (评测集 80 条 + 离线评测脚手架, 1d) | 也要等 BE-S2-007，主循环跑通才有"召回@5 / 幻觉率 / LLM-as-judge" 输入 |

→ **建议下一步走 BE-S2-007**（LangGraph 主循环 + 引用源装配：spec Agent 主线 BE-S2-006a ✅ → BE-S2-006b ✅ → **BE-S2-007** → BE-S2-008。BE-S2-007 PR 内会落地：`app/services/agent/graph.py`（LangGraph StateGraph 主循环：node 1 plan = LLM with tools call 决策, node 2 act = 拿 tool_calls → 走 tool_registry.get(name).runner(args, session=...) 并行调用; node 3 reflect = 把 tool messages 喂回 LLM 决定再循环 / 收尾, 最多 N 轮）+ `agent/citation.py`（引用源装配: 把每个 chunk 的 doc_id / page → ``[1] 招股书 P12`` 编号 + sources 数组返回端层）+ `app/api/v1/chat.py`（POST /v1/chat/diagnose SSE 端层: 监控 token usage 写入 chat_token_usage + tool calls 写入 chat_tool_calls + 每条 message 末尾 append DISCLAIMER）+ E2E 集成测试）

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

- [x] `hkex_client.fetch_hk_applicants(...) -> HKApplicantFetchResult`：从 hkexnews `applicants_c.htm` 解析 `name` / `submission_date`（→ `subscribe_start`）/ `prospectus_url`，含占位 `code`（详见决策 7）
- [x] 失败兜底：网络 5xx / HTML 改版 / 解析失败时 `logger.warning` + 返回空 result，不 raise（与 BE-007 A 股路径一致）
- [x] 速率限制：`asyncio.Semaphore(N)` 默认 `N=2` + `httpx.AsyncClient(timeout=10)` 防长 hang
- [x] `run_ingest_hk_job()`：调 `fetch_hk_applicants` → `upsert_ipos(extra_per_code={"prospectus_url": ...})` → `invalidate_namespace("ipos:list", "ipos:detail")`
- [x] APScheduler 每天跑两次（HK IPO 节奏比 A 股慢，cron 默认 `9,17` 时区 `Asia/Hong_Kong`，开盘前 + 收盘后二刀流；`scheduler/__init__.py` 单 pod 同时挂 A + HK）
- [x] `ipo_service.list_ipos(market="HK")` / `get_ipo` / `get_ipo_detail` 切 DB 路径，DB 空表（lifespan 第一次启动 / 测试无 ingest）走 `hkex_client.get_cold_start_seed` 冷启动兜底
- [x] 单测 ≥ 5 条 → 实际 13 条新增（`tests/test_hkex_client.py` 9 条 + `tests/test_ipo_ingest.py` 4 条 HK 路径），覆盖 happy / 列表为空 / 无表 / 无链接 / 空名 / `limit` 截断 / 5xx / 网络 error / extra 合并守护 BE-S2-004 字段 / cron 注册

### 关键设计决策（提前定）

1. **`code` 格式统一为 `XXXXX.HK`（5 位补零）**：与 Sprint 1 favorites store / FE-005 详情页一致，不引入新格式（hkexnews 原始展示是无后缀的 `0700`，adapter 层补 `.HK`）
2. **`prospectus_url` 存进 `extra` 而非顶层列**：BE-009 `IPODetail.prospectus_url` 已从 `extra` 读，走同一存储格式不动 schema
3. **不在本 PR 做招股书 PDF 下载**：那是 BE-S2-004 的职责（要做切分 + embedding）；BE-S2-000 只负责把 PDF URL 入库，文件下载不在范围内
4. **HK 与 A 股共享同一份 cron 注册函数**：`scheduler/register_jobs` 内部按 `if settings.scheduler_enabled: register a + register hk` 判断，单 pod 配置即开
5. **保留 seed 作为 cold-start fallback**：DB 空表（lifespan startup 第一次跑）时 `ipo_service.list_ipos(market="HK")` 还能拿到 3 条样例，不让首次部署的用户看到空首页
6. **复用 Sprint 1.5 的 cache invalidation**：`run_ingest_hk_job` 末尾 `await invalidate_namespace("ipos:list", "ipos:detail")` —— 这就是 Sprint 1.5 收尾包给 Sprint 2 留下的复用价值，HK 写完 ipos 表后立即让缓存回源，前端 / RAG 都看到最新
7. **占位 `code` = `AP{yymmdd}{slug:5}.HK`（16 字符 ≤ `VARCHAR(16)`）**：hkexnews applicants 阶段没正式股票号（要等 listing 才有 `00xxxx`），主键又必须非空，因此造一个稳定可逆的 placeholder。`yymmdd` 自递交日，`slug` 取 ASCII 名前 5 字符或中文名 sha1 前 5 hex；递交日改 / 重抓时是同一 key 命中 `ON CONFLICT` 刷新 `prospectus_url`。BE-S2-004 拿到正式 `00xxxx.HK` 时再写一条 row（旧 `AP*` 留作历史轨迹）。
8. **`extra` JSONB merge（关键守护）**：`upsert_ipos` 之前直接 `extra: excl.extra` → ON CONFLICT 时会**整体覆盖**导致 BE-S2-004 写的 `highlights` / `risks` 被擦掉。改成 `func.coalesce(cur.extra, sa_text("'{}'::jsonb")).op("||")(excl.extra)`，PG 原生 `jsonb || jsonb` 浅合并，新 key 写入、老 key 保留。

### 实施成果（2026-04-26 落地）

**改动文件**

| 文件 | 类型 | 说明 |
|------|------|------|
| `apps/api/app/adapters/hkex_client.py` | 新建 380 行 | `HKApplicantFetchResult` + `parse_applicants_html` + `fetch_hk_applicants_with_client` + 占位 code 生成 + cold-start seed |
| `apps/api/app/adapters/akshare_client.py` | deprecated | `fetch_hk_ipos` 转发到 `hkex_client.get_cold_start_seed`；`_HK_SEED` 删 |
| `apps/api/app/services/ipo_ingest_service.py` | +90 行 | `run_ingest_hk_job` + `extra` JSONB merge 守护（关键 bugfix） |
| `apps/api/app/services/ipo_service.py` | +60 行 | HK 切 DB；DB 空表回退 cold-start seed |
| `apps/api/app/scheduler/__init__.py` | +30 行 | `ipo_ingest_hk_initial` + `ipo_ingest_hk_cron` 二刀流，独立 timezone |
| `apps/api/app/core/config.py` | +7 字段 | `hkex_base_url` / `ipo_ingest_hk_*`（cron / 时区 / 并发 / 超时）|
| `apps/api/.env.example` | sync | 上述 7 字段示例 |
| `apps/api/pyproject.toml` | +1 dep | `beautifulsoup4>=4.12` 提为直接依赖 |
| `apps/api/tests/test_hkex_client.py` | 新建 9 测 | 解析层 6 + HTTP 层 3 |
| `apps/api/tests/test_ipo_ingest.py` | +6 测 | HK happy + extra-merge 守护 + 错误吞 + 空结果 + 注册 cron + 0 delay |
| `apps/api/tests/test_ipos_list.py` | 调整 + 新增 8 测 | DB 路径 / 冷启动回退 / 过滤跳过回退 / DB 优先 |
| `apps/api/tests/test_ipo_detail.py` | 调整 + 新增 2 测 | DB 详情 / 冷启动详情 / 404 |

**测试结果**
- `make test-all`: 273 passed in ~33s（Sprint 1 收官 251 → BE-S2-001 +6 → BE-S2-003 +8 → BE-S2-000 +17 - 历史 HK seed 测试合并淘汰 = 273）
- `ruff` 在 BE-S2-000 触动文件: 0 增量；全局 `ruff check` 从 54 顺手清到 46（修了 8 个 BE-S2-000 触动文件的 import 排序 / `try/except/pass` → `contextlib.suppress` / `timeout` 改名 `request_timeout` 避 ASYNC109）
- `mypy` 在 BE-S2-000 触动文件: 0 增量（baseline 23 维持，pre-existing pandas / sqlalchemy `__table__` 类型问题不在 BE-S2-000 引入）

**关键 bug & 修复（PR 内自查发现）**
1. `StringDataRightTruncationError`: 占位 code 原设计 `AP-20260301-LIBANG00.HK` 24 字符，超 `ipos.code VARCHAR(16)` 上限 → 改 `AP{yymmdd}{slug:5}.HK` 16 字符贴边（不动 schema 避免 user_favorites FK 迁移）
2. `extra` 整体覆盖问题: 见决策 8，加了 4 条 regression 测试守护 BE-S2-004 RAG 字段不被 ingest 擦掉
3. ASYNC109 lint: `fetch_hk_applicants_with_client(timeout=...)` 触发 ruff async 警告 → 改名 `request_timeout`，本质是把超时透传给 `httpx.AsyncClient.get()` 自己处理

### 不做（明确 P1+）

- HK 一手中签率历史（hkexnews 没这字段，要爬 [aastocks](https://www.aastocks.com) 或 [moomoo](https://moomoo.com) → 反爬 + 数据合规风险，Sprint 3 接 Futu 时一起做）
- 暗盘行情 / 灰盘价（券商数据，需付费，Sprint 4 VIP 增值功能）
- 港股新股辅助计算器（认购倍数 / 中签预估，Sprint 3 业务功能）

---

## BE-S2-004 详细规格（落地版）

### 改动文件

```
apps/api/app/adapters/pdf_loader.py                          # 新建
apps/api/app/services/rag/__init__.py                        # 新建包
apps/api/app/services/rag/chunker.py                         # 新建
apps/api/app/services/rag/prospectus_ingest_service.py       # 新建
apps/api/app/core/config.py                                  # +4 字段
apps/api/.env.example                                        # 同步
apps/api/pyproject.toml                                      # +pypdf>=5.0
apps/api/tests/test_pdf_loader.py                            # 新建 9 测
apps/api/tests/test_chunker.py                               # 新建 16 测
apps/api/tests/test_prospectus_ingest.py                     # 新建 8 测 (DB 真跑)
spec/09-sprint-2-backlog.md                                  # 状态 + 实施成果
README.md / apps/api/README.md                               # 同步
```

### 三层架构（Adapter / Service / Service-Orchestrator）

```
prospectus_ingest_service.run_ingest_prospectus(session, ipo_code, prospectus_url, lang)
  │
  ├─[stage 1 fetch]→ pdf_loader.fetch_pdf_bytes(url, max_size_mb, request_timeout)
  │                  │ httpx.stream + Content-Length 提前拒 + 累计字节兜底
  │                  └→ raises PDFFetchError
  │
  ├─[stage 2 extract]→ pdf_loader.extract_text_per_page(pdf_bytes)
  │                    │ pypdf 6.x PdfReader; 单页失败 logger.warning + skip
  │                    └→ PDFExtractResult{pages=[(pno, text), ...], total_pages, extracted_pages}
  │
  ├─[stage 3 chunk]→ chunker.split_text(full_text, max_tokens=500, overlap_tokens=50)
  │                  │ 段落边界优先 → 句子边界 → 字符硬截 (3 层 fallback)
  │                  │ 估 token: CJK 1:1, 英文 4:1
  │                  └→ list[Chunk{text, token_count, char_start, char_end}]
  │
  ├─[stage 4 embed]→ llm_client.embed(texts) (BE-S2-002)
  │                  │ 自动 batch 32, 1024-d, dim 校验
  │                  └→ EmbeddingResult{embeddings, dim, usage, model, provider}
  │
  └─[stage 5 db]→ INSERT INTO ipo_documents ... ON CONFLICT (doc_id, content_hash)
                  │   WHERE content_hash IS NOT NULL DO NOTHING
                  │ doc_id = sha256(prospectus_url)[:32]
                  │ content_hash = sha256(chunk.text) (64 hex)
                  └→ stats{inserted, skipped_duplicates, errors, stage}
```

### 关键设计决策（提前定）

1. **不引 LangChain / LlamaIndex**：项目走精简包路线 (spec/06)，splitter / embedder / vectorstore 三抽象都自己写。LangChain v0.3 包体 50+ MB 不划算
2. **PDF 库选 pypdf 5.x（非 pdfplumber / pdfminer）**：招股书是数字化 PDF（无 OCR 需求），pypdf 5.x 文本抽取够用 + 纯 Python；pdfplumber 强在 form/table 但当前 RAG 不做表抽取（财务表格等 Sprint 3+ 单立 schema）
3. **token 估算用启发式（非 tiktoken / bge-m3 真 tokenizer）**：tiktoken 是 OpenAI 私有切分器，与 bge-m3 BPE 不同；bge-m3 tokenizer 装 transformers + sentencepiece 太重。`token_count` 列只用于 cost 调试，CJK 1:1 + 英文 4:1 偏差 ±15% 不影响切分逻辑
4. **`doc_id = sha256(url)[:32]`（而非 `(ipo_code, version)`）**：URL 即文档身份；URL 变（招股书改版）→ doc_id 变 → 新旧版本 chunk 独立共存（不主动删旧）。32 hex 贴 `String(64)` 留 buffer
5. **失败一律 logger.exception + 计 stats，不抛**：与 BE-007 / BE-S2-000 一致；调度任务 / Tool 调用方读 `stats["stage"]` 决定 retry 策略，不让 unhandled exception 把 LangGraph 链路打断
6. **本 PR 不挂 APScheduler**：招股书 PDF 几十 MB × N 只新股，lifespan startup 自动跑会撑爆带宽 / 临时盘。改成手动入口 `run_ingest_prospectus`，Sprint 3 给运营加触发面板 + 速率控制
7. **本 PR 不做 A 股招股书**：监管反爬（巨潮 / 上交所） + 经常被改版 + 合规争议，spec/04 已写"HK 是 RAG MVP 主战场"。A 股 Sprint 3+ 由人工提交 PDF 路径
8. **partial UNIQUE 的 `index_where` 必给**：BE-S2-003 索引是 `(doc_id, content_hash) WHERE content_hash IS NOT NULL`；PG `ON CONFLICT` 路由到 partial 索引时**必须复述同样的谓词**，否则 `InvalidColumnReferenceError: no constraint matching`。本 PR 内自查发现的 bug，已加 `index_where=sa_text("content_hash IS NOT NULL")`
9. **embed dim 校验提前置**：embed 返回 `dim=512` 而非 `1024` 时直接 stage=embed 兜底退出，不让维度污染 vector(1024) 索引（BE-S2-005 检索时 cosine 距离会算错）
10. **事务边界由调用方控制**：service 函数内不 commit / rollback，单元测试在事务里跑完回滚 + 上层批量灌库可控制单份独立事务还是合并提交

### 测试矩阵（33 条新增）

| 测试文件 | 用例数 | 覆盖 |
|---------|-------|------|
| `test_pdf_loader.py` | 9 | extract happy / 多页顺序 / empty / 损坏 / fetch happy / 404 / 超尺寸 (Content-Length) / 超尺寸 (无 CL 流式) / 网络错误 |
| `test_chunker.py` | 16 | estimate_tokens 4 例（空 / 纯 CJK / 纯英文 / 混合）/ split_text 12 例（入参校验 4 + 段落边界 + 长段落按句切 + overlap 行为 + char offset 单调 + CJK 中文段 + 单 word 硬截 + 短文本直通） |
| `test_prospectus_ingest.py` | 8 | full pipeline happy / 重跑 dedup / fetch 失败 / corrupt 失败 / embed 失败 / orphan ipo_id / dim mismatch / ipo_id resolve 已存在 |

### 实施成果（2026-04-26 落地）

| 维度 | Before BE-S2-004 | After BE-S2-004 |
|------|------|------|
| pytest | 273 passed | **306 passed** (+33) |
| ruff | 46 errors | 46 errors（持平，BE-S2-004 触动文件 0 增量） |
| mypy | 23 errors | 23 errors（持平，BE-S2-004 0 增量；本 PR 内自查时给 prospectus_ingest 加了 4 处 cast/annotation） |
| 新建文件 | — | `pdf_loader.py` 195 行 + `chunker.py` 207 行 + `prospectus_ingest_service.py` 290 行 + 3 个测试文件 |
| 配置项 | 35 字段 | 39 字段（+`pdf_max_size_mb` / `pdf_request_timeout_seconds` / `rag_chunk_size_tokens` / `rag_chunk_overlap_tokens`）|

### 关键 bug & 修复（PR 内自查发现）

1. `InvalidColumnReferenceError: no constraint matching` — 见决策 8，partial UNIQUE 的 `index_where` 必给
2. `estimate_tokens` 纯 CJK 时多 +1 token — 公式 `cjk + max(1, other//4)` 在 `other_chars=0` 时强制 +1；改成 `other_chars > 0 才计 ceil(other_chars/4)`，且最小保底 1 token 走"纯 ASCII 短串"分支
3. mypy `page_no` 重定义 — `_locate_chunk_page(chunk, ...)` 返回值变量名与上方 `for pno, page_text in extract_result.pages` 冲突；改 outer 循环变量为 `pno`

---

### BE-S2-005 实施成果（2026-04-26 落地）

**改动文件**

| 文件 | 类型 | 说明 |
|------|------|------|
| `apps/api/alembic/versions/0004_ipo_documents_fts.py` | 新建 90 行 | tsvector 生成列 + GIN 索引 (CJK 字符级预切 + simple config) |
| `apps/api/app/services/rag/hybrid_search.py` | 新建 380 行 | `hybrid_search` 主入口 + RRF 融合 + rerank fallback + SearchResult / HybridSearchOutput |
| `apps/api/app/services/rag/__init__.py` | +1 export | 暴露 `hybrid_search` 子模块 |
| `apps/api/app/core/config.py` | +6 字段 | `rag_vector_top_k` / `rag_bm25_top_k` / `rag_rrf_k` / `rag_rerank_pool_size` / `rag_final_top_k` / `rag_use_rerank` |
| `apps/api/.env.example` | 同步 | 上述 6 字段示例 |
| `apps/api/tests/test_hybrid_search.py` | 新建 19 测 | CJK 预切 + RRF 单元 + 6 大 DB 集成场景 |
| `apps/api/tests/integration/test_document_chunks_schema.py` | +2 测 / 修 1 测 | 0004 生成列 + GIN 检查 + tsv BM25 召回 + downgrade 适配多 head |
| `spec/09-sprint-2-backlog.md` | 状态 + 实施成果 | 记录 BE-S2-005 落地 |
| `README.md / apps/api/README.md` | 同步 | README 更新 |

**三阶段架构（vector + BM25 + RRF + rerank）**

```
hybrid_search(session, query, *, ipo_code, doc_type, lang,
              vector_top_k=50, bm25_top_k=50, rrf_k=60,
              rerank_pool=20, final_top_k=5, use_rerank=True,
              query_embedding?=None, settings?=None)
  │
  ├─[stage A vector]→ embed(query) [可注入 query_embedding]
  │                   → SQL: embedding <=> CAST(:q_emb AS vector)
  │                          ORDER BY <=> ASC LIMIT :vector_top_k
  │                          (HNSW cosine / WHERE embedding IS NOT NULL
  │                           AND embedding_dim = :emb_dim AND filters)
  │                   → list[row]; 失败 → vector_failed=True 并仅走 BM25
  │
  ├─[stage B bm25]→ _cjk_presplit(query) → plainto_tsquery('simple', ...)
  │                 → SQL: tsv @@ plainto_tsquery('simple', :q_text)
  │                        ORDER BY ts_rank_cd DESC LIMIT :bm25_top_k
  │                        (GIN tsv / 同样 filters)
  │                 → list[row]; query 全标点 → 跳 BM25; 失败 → bm25_failed=True
  │
  ├─[stage C RRF]→ score(d) = Σ 1/(rrf_k + rank_i(d))  for i in {vec, bm25}
  │                融合 unique chunk_id → 注入 rrf_score / vector_rank / bm25_rank
  │                → 按 rrf_score DESC + chunk_id ASC 稳定排序
  │
  └─[stage D rerank]→ POST /v1/rerank (query, top rerank_pool docs)
                      → 重排 top_n=final_top_k → 返回 (orig_idx, score)
                      → 失败 → logger.warning + fallback 走 RRF 顺序
                      (use_rerank=False 直接跳过, 单测 / CI 默认走这条)
```

**关键设计决策（提前定）**

1. **不上 zhparser**：装难（sudo make + scws 字典）+ CI 跑不了 + 字典维护重；用 PG ``simple`` config + 中文字符级预切替代。**单一真相**：写入端 0004 migration 用 `regexp_replace(text, E'([\u4e00-\u9fff])', E'\\1 ', 'g')` 生成 tsv，查询端 `_cjk_presplit` 用同样正则。两边偏差 = 0
2. **CJK 字符级 BM25 baseline 召回率高 / 精度低**：刚好对位 RRF + cross-encoder rerank 二阶段 —— 让向量 + reranker 把精度补回来，用 BM25 兜召回。spec/04 §核心壁垒"混合检索"原始设计意图就是这样
3. **Vector / BM25 SQL 分别打**（不走 UNION ALL）：HNSW 与 GIN 走各自 planner 计划，不互相干扰；应用层做 RRF 也方便 mock + 单测
4. **rrf_k=60 锁定 Cormack 2009 经验值**：这是 IR 领域事实标准；越大平滑（rank 1 vs rank 50 差距小），越小突出头部（容易被两路前 1 名 overdominate）
5. **Pool size 锁 20 不锁 50**：rerank 是 cross-encoder（双输入过 Transformer），成本是 embedding 的 50-100x；20 是性价比拐点。final_top_k=5 对齐 spec/04 P0 KPI "top5 引用源"
6. **rerank 失败 → fallback RRF**：硅基流动 quota / 网络抖动不让整条链路断；CI 默认 `rag_use_rerank=False` 也走这条，省 API key 调用
7. **embedding 维度强校验**：`WHERE embedding_dim = :emb_dim` 推 SQL，把多版本 embedding（将来 bge-m4 共存）默认隔离；query embedding 维度 ≠ settings 时直接 vector_failed → BM25 兜底
8. **过滤推 SQL（不走应用层）**：`ipo_code` / `doc_type` / `lang` 全在 WHERE，利用现有 `(ipo_code, doc_type)` btree 索引；应用层只负责 RRF 融合 + rerank
9. **不在本 PR 做 query rewrite / HyDE**：那些是 BE-S2-007 LangGraph 主循环里干（有 LLM context 才能改写）；本层只做单 query 检索原语
10. **不在本 PR 做语义缓存**：检索 query 命中率低，加缓存收益小；BE-S2-007 LangGraph 轮内 cache 已够用
11. **ORM 不反映 tsv 列**：generated column read-only，BM25 走 raw SQL；ORM INSERT 时字段不在映射里 = 不会被写入 = PG 自动按生成表达式填值 = 业务代码 0 改动
12. **session 不做事务管理**：read-only path，调用方决定 session 生命周期，方便测试 + 上层批量 search 复用同一 session

**测试矩阵（21 条新增 = 19 hybrid_search + 2 schema）**

| 测试文件 | 用例数 | 覆盖 |
|---------|-------|------|
| `tests/test_hybrid_search.py` | 19 | `_cjk_presplit` 4 + `_rrf_fuse` 3 + DB 集成 12（vector-only / bm25-only / RRF 融合 / ipo_code 过滤 / doc_type 过滤 / rerank reorder / rerank fail fallback / 空 query / whitespace / dim mismatch fallback / 全标点 query / final_top_k 截断）|
| `tests/integration/test_document_chunks_schema.py` | +2 | 0004 schema (tsv generated ALWAYS / 含 to_tsvector simple + regexp_replace 表达式 / GIN 索引在 tsv 上) + tsv BM25 中英双语真实召回 |

**实施成果（2026-04-26 落地）**

| 维度 | Before BE-S2-005 | After BE-S2-005 |
|------|------|------|
| pytest | 306 passed | **327 passed** (+21) |
| ruff | 46 errors | 46 errors（持平，BE-S2-005 触动文件 0 增量） |
| mypy | 23 errors | 23 errors（持平，BE-S2-005 0 增量） |
| 新建文件 | — | `0004_ipo_documents_fts.py` 90 行 + `hybrid_search.py` 380 行 + 1 个 19 测试文件 + 1 个补 2 测试 |
| 配置项 | 39 字段 | 45 字段（+6 RAG 检索参数） |
| Alembic head | 0003_chunks | **0004_fts** |

**关键 bug & 修复（PR 内自查发现）**

1. **`test_0003_downgrade_then_upgrade_is_idempotent` 退到错版本** — 原 `command.downgrade(cfg, "-1")` 当 head=0003 时回 0002（无 RAG 列）；现 head=0004 后 `-1` 只回 0003（仍有 RAG 列）→ assert 失败。改成 `command.downgrade(cfg, "0002_chat")`，显式 revision 不再依赖 `head - 1` 偏移，未来 0005/0006 加进来也不需再改
2. **`SIM108` ternary 简化** — `_seed_chunk` 的 `emb_param` 初始化 if/else 换三元表达式
3. **`F401` 未用 import** — `tests/test_hybrid_search.py` 删 `from app.adapters import llm_client`（已经从子路径单独 import）

---

## 🛡 Sprint 2 不能碰的事

- 真实 LLM Key 写到代码里（永远走 `.env` + `fake_llm` fixture mock）
- 招股书全文存到 `chat_messages.content`（侵犯版权 + 表暴涨）→ 只存 chunk reference
- 删除 Sprint 1 的 `agent_service.diagnose_stream` 单 shot 路径（保留作为 Tool Use 失败兜底，至少留到 Sprint 3 才砍）
- 在 `agent.py` 路由层加 `get_current_user` 强制鉴权（spec/04 §1.3 允许匿名；要改先改 spec）
- 任何 `pg_dump` / 真实生产数据进 `evals/` 目录（评测集必须是合成 / 公开 / 已脱敏数据）

### BE-S2-006a 实施成果（2026-04-26 落地）

**改动文件**

| 文件 | 类型 | 说明 |
|------|------|------|
| `apps/api/app/services/agent/__init__.py` | 新建 26 行 | agent 包入口 + 显式 import 触发子包 side effect |
| `apps/api/app/services/agent/tool_registry.py` | 新建 175 行 | `Tool` / `ToolResult` dataclass + 模块级 `_REGISTRY` + register / get / list_all / list_openai_schemas / unregister / clear_registry_for_test |
| `apps/api/app/services/agent/sandbox.py` | 新建 130 行 | `@sandboxed(input_model, timeout_seconds)` 装饰器（pydantic 入参校验 + asyncio.wait_for 超时 + 异常归一 + elapsed_ms 注入 + deps 透传） |
| `apps/api/app/services/agent/tools/__init__.py` | 新建 30 行 | side effect import basic_info / financial 子模块 |
| `apps/api/app/services/agent/tools/basic_info.py` | 新建 110 行 | `get_ipo_basic_info` Tool（对接 `ipo_service.get_ipo`, Decimal/date 序列化） |
| `apps/api/app/services/agent/tools/financial.py` | 新建 105 行 | `get_financial_statements` Tool（对接 `ipo_service.get_ipo_detail.extra.financial_summary`, 缺数据走 warning 不算失败） |
| `apps/api/tests/test_tool_registry.py` | 新建 15 测 | ToolResult / Tool.to_openai_schema / register-get-list-unregister / 重名替换 + warning / name 不合法 / runner 非 callable / input_model 非 BaseModel / 默认 Tool 自注册 |
| `apps/api/tests/test_agent_sandbox.py` | 新建 12 测 | sandboxed happy / pydantic ValidationError 归一 / TimeoutError / 通用 Exception 归一 / 契约违反 / deps 透传 / elapsed_ms 复写 |
| `apps/api/tests/test_basic_info_tool.py` | 新建 8 测 | basic_info happy / minimal item / not_found / 入参校验 / 上游异常归一 / OpenAI schema 形状 |
| `apps/api/tests/test_financial_tool.py` | 新建 11 测 | financial happy / financial_summary 缺失 → warning / wrong type → 兜底 / highlights 类型异常 / not_found / years 越界 / years 默认值 / 上游异常 / OpenAI schema |
| `spec/09-sprint-2-backlog.md` | 状态 + 实施成果 + 下一步推荐 | 记录 BE-S2-006a 落地, 下一步指向 BE-S2-006b |
| `README.md / apps/api/README.md` | 同步 | README 更新（373 passed） |

**架构（Tool Use 第 1 层）**

```
LLM tool_call (OpenAI tools schema)
     │
     ▼
list_openai_schemas() ←── 注册中心 _REGISTRY
     │                        ▲
     ▼                        │ register(Tool(...))
Tool.runner(raw_args)  ◄──┐   │
     │                    │   │
     ▼                    │   │
  @sandboxed              │  basic_info / financial
    ├─ pydantic 入参校验   │   tools/__init__.py 模块 import
    ├─ asyncio.wait_for   │   时 side effect 自动注册
    ├─ 异常归一            │
    └─ elapsed_ms 注入     │
     │                    │
     ▼                    │
   原 runner ──────────────┘
     │
     ▼
ToolResult(ok / data / error / elapsed_ms)
     │
     ▼
LLM tool message  (BE-S2-007 主循环 json.dumps 入 chat_tool_calls)
```

**关键设计决策（提前定）**

1. **Tool 协议走 OpenAI tools schema**（`type: function` + `name` + `description` + `parameters` JSON schema）—— DeepSeek-V3 / Qwen / 智谱 GLM-4 全兼容；spec/04 §3.1 原文就是这套，BE-S2-007 LangGraph 不需要再做协议适配
2. **不上 LangChain `BaseTool` / `StructuredTool`**：包体大 + 抽象 4 层，60 行自己写更可控；spec/06 走"精简包路线"
3. **入参 schema = pydantic BaseModel + `model_json_schema()`**：一处定义 = 入参文档 + 入参校验 + LLM schema 三合一；新加 Tool 只需要 1 个 BaseModel + 1 个 async runner
4. **沙盒走装饰器（不是注册中心硬包）**：让每个 Tool 文件**自包含**，看 Tool 实现就能知道它的 input model + 超时；注册中心 `Tool.runner` 类型签名仍只是 `ToolRunner`，BE-S2-007 主循环不感知是否被装饰过
5. **side effect 注册（不是显式 register list）**：BE-S2-006b / BE-S2-007 后会有 5+ Tool，写显式 list 容易漏；side effect import 让"加 Tool" = "加文件 + 加 import 一行"
6. **重名替换 + warning（不抛）**：热重载 / 单测重导包友好；测试用 `clear_registry_for_test()` + `importlib.reload(tools_pkg.basic_info)` 重置
7. **异常**绝不**冒上 LangGraph**：runner 内任何 exception 在沙盒兜底为 `ToolResult.failure`；只露 `Exception.__class__.__name__` 给 LLM（堆栈 + message 进 logger.exception，避免 LLM 学习反向越权）
8. **Decimal / date / datetime 序列化在 Tool 内做**：LLM 不识别 Decimal，提前 cast 成 float / ISO string 避免 OpenAI client 再次序列化时的 `decimal.Decimal is not JSON serializable` 报错
9. **Tool 内不写 `chat_tool_calls` 表**：归 BE-S2-007 LangGraph 主循环（在那里有 chat_session_id / step_index / 真实 LLM tool_call_id），Tool 只管"算结果"
10. **`get_financial_statements` 缺数据 ≠ 调用失败**：返回 `ok=True` + `financial_summary=None` + warning 提示走 `hybrid_search`；让 LLM 决定是改 Tool 还是回答"暂无数据"，符合 spec/04 §3.3 防幻觉中"数据缺失必须明确说"的要求
11. **`years` 当前仅 metadata 透传**：未接 AKShare 时不切片 financial_summary，只把入参回写给 LLM；BE-S3 接 AKShare 后再实际裁剪 `revenue_3y[:years]`
12. **OpenAI tool name 约束**（`^[a-zA-Z0-9_-]{1,64}$`）注册时强校验：防止后续 Tool 起名带空格 / 中文导致 LLM provider 拒收

**Tool 清单（spec/04 §3.1 对齐）**

| Tool name | 状态 | 说明 |
|-----------|------|------|
| `get_ipo_basic_info` | ✅ BE-S2-006a | IPO 基础信息（发行价 / PE / 募资额 / 上市日期 / 行业 / 状态） |
| `get_financial_statements` | ✅ BE-S2-006a | 财务摘要（financial_summary / highlights / risks）+ 缺数据 warning |
| `get_peer_comparison` | ✅ BE-S2-006b | 同业对标（industry_l2 优先 → industry_l1 fallback, 5 dim metrics） |
| `get_sentiment_summary` | ✅ BE-S2-006b | 情感分布 placeholder（counts=0 + warning, 留口 BE-S3 接文章源） |
| `get_historical_winning_rate` | ✅ BE-S2-006b | 历史中签率聚合（industry / sponsor / year_range, 走 ipos.extra JSONB） |
| `hybrid_search` | ✅ BE-S2-006b | 包装 BE-S2-005 hybrid_search 函数为 Tool（session deps 注入 / 默认走 factory） |

**测试矩阵（46 条新增）**

| 测试文件 | 用例数 | 覆盖 |
|---------|-------|------|
| `tests/test_tool_registry.py` | 15 | ToolResult success/failure/frozen + Tool.to_openai_schema 形状（含 strip title）+ register/get/list_all/unregister + 重名替换 warning + name/runner/input_model 校验 + clear_registry_for_test + 默认 Tool 自注册（含 importlib.reload fixture 隔离） |
| `tests/test_agent_sandbox.py` | 12 | happy / pydantic 缺字段 / 类型不匹配 / 约束违反 / None args / TimeoutError / ValueError 归一 / RuntimeError 归一 / 契约违反（runner 不返回 ToolResult） / deps 透传（multiplier kwarg） / elapsed_ms 沙盒覆写 |
| `tests/test_basic_info_tool.py` | 8 | happy（含 Decimal→float / date→ISO 序列化校验）/ minimal item（多字段 None）/ not_found / 空 code / 短 code / 长 code / 上游 RuntimeError 归一 / OpenAI schema 形状 |
| `tests/test_financial_tool.py` | 11 | happy（含 financial_summary 透传）/ summary 缺失 warning / summary wrong type 兜底 / highlights 类型异常兜底为 [] / not_found / years 越界（0 / 10）/ years 缺失 default 3 / 上游异常 / OpenAI schema |

**实施成果（2026-04-26 落地）**

| 维度 | Before BE-S2-006a | After BE-S2-006a |
|------|------|------|
| pytest | 327 passed | **373 passed** (+46) |
| ruff | 46 errors | 46 errors（持平，BE-S2-006a 触动文件 0 增量） |
| mypy | 23 errors | 23 errors（持平，BE-S2-006a 触动文件 0 增量） |
| 新建文件 | — | `services/agent/__init__.py` + `tool_registry.py` + `sandbox.py` + `tools/__init__.py` + `tools/basic_info.py` + `tools/financial.py` 6 src 文件 + 4 测试文件 |
| 已注册 Tool 数 | 0 | **2** (`get_ipo_basic_info` / `get_financial_statements`) |
| Alembic head | 0004_fts | 0004_fts（BE-S2-006a 不动 schema） |

**关键 bug & 修复（PR 内自查发现）**

1. **`caplog` 抓不到 loguru warning** — loguru 不通过 stdlib logging，pytest `caplog` / `capfd` 都拿不到（loguru sink 在 module import 时已经持有了原始 stderr 引用）。改用 `monkeypatch.setattr(registry_mod.logger, "warning", _capture)` 直接 patch logger 方法，单测可控
2. **`importlib.reload(tools_pkg)` 不会触发子模块 side effect** — Python module 缓存命中后 reload 父包不重跑子包 import；改 `importlib.reload(tools_pkg.basic_info)` + `importlib.reload(tools_pkg.financial)` 显式重跑两个子模块，让 `clear_registry_for_test` 后能恢复初始 2 Tool 状态
3. **OpenAI 自托管 LLM (Qwen-2.5) 解析 `title` 字段挑剔** — pydantic `model_json_schema()` 默认带 `title` 字段，OpenAI 接收但部分自托管 LLM 报错；`Tool.to_openai_schema()` 主动 `params.pop("title", None)` 防御
4. **`raw_args=None` 与 `raw_args={}` 必须等价** — LLM tool_call 没参数时可能传 None，沙盒入参解码层必须接住，否则 pydantic 直接抛 AttributeError；改成 `raw_args = raw_args or {}` 归一为 dict 后再 validate
5. **runner 内部声明的 `elapsed_ms` 必须被沙盒覆写** — runner 实现可能拷错或忘改 elapsed，让真实计时统一在沙盒侧（dataclass `replace` 风格重建 ToolResult）

### BE-S2-006b 实施成果（2026-04-26 落地）

**改动文件**

| 文件 | 类型 | 说明 |
|------|------|------|
| `apps/api/app/services/agent/tools/peers.py` | 新建 220 行 | `get_peer_comparison` Tool（同 industry_l2 优先 → industry_l1 fallback；PE 直接列 + PB/ROE/GrossMargin/Revenue 从 `extra.financial_summary` 提；Literal 5 维约束） |
| `apps/api/app/services/agent/tools/sentiment.py` | 新建 100 行 | `get_sentiment_summary` placeholder Tool（counts=0 + `data_source_status="not_connected"` + warning；BE-S3 接文章源后只换实现不动 schema） |
| `apps/api/app/services/agent/tools/historical.py` | 新建 215 行 | `get_historical_winning_rate` Tool（走 `ipos.extra->>'one_lot_winning_rate'` 聚合 avg/min/max/count；industry / sponsor (jsonb @>) / year_range 三个 optional 过滤；first_day_performance=null 留口 BE-S3） |
| `apps/api/app/services/agent/tools/hybrid_search.py` | 新建 195 行 | `hybrid_search` Tool（包装 BE-S2-005 `services/rag/hybrid_search` 函数；deps 注入 session, 不传走 `get_session_factory()`；UUID/SearchResult 序列化；空结果 warning） |
| `apps/api/app/services/agent/tools/__init__.py` | 修改 | 追加 4 个新模块 side effect import（peers / sentiment / historical / hybrid_search） |
| `apps/api/tests/integration/conftest.py` | 修改 | `patch_session_factory` targets 扩展 3 个新模块（peers / historical / hybrid_search），让 `get_session_factory()` 模块拷贝在测试时拉到测试库 |
| `apps/api/tests/test_sentiment_tool.py` | 新建 7 测 | placeholder 形状 / code upper().strip() 归一 / window_days 默认 7 / window_days 越界 / OpenAI schema |
| `apps/api/tests/test_hybrid_search_tool.py` | 新建 9 测 | happy（显式 session 注入 + ipo_code 大写归一 + UUID→str 序列化）/ 不传 session 走 factory / 空结果 warning / lang 透传 / 入参校验 / 上游异常归一 / OpenAI schema title 剔除 |
| `apps/api/tests/integration/test_peers_tool.py` | 新建 9 测 | l2 优先排序 + 排除自己 / l1 fallback 凑齐 limit / 该行业第一只→空 peers + warning / target 不存在 → failure / 自定义 dimensions 子集 / Literal dim 拒收 / code 校验 / limit 边界 / OpenAI schema |
| `apps/api/tests/integration/test_historical_tool.py` | 新建 13 测 | industry 过滤聚合 / industry_l1 与 l2 都匹配 / sponsor jsonb @> 过滤 / year_range 单年 / year_range 闭区间 / 全市场 / 命中 0 warning / 全 NULL warning / 排除非 listed / year_range 长度/顺序/越界校验 / OpenAI schema |
| `spec/09-sprint-2-backlog.md` | 状态 + 实施成果 + 下一步 | 记录 BE-S2-006b 落地, 下一步指向 BE-S2-007 |
| `README.md / apps/api/README.md` | 同步 | README 更新（411 passed, 6 个 Tool 全数注册到位） |

**架构（Tool Use 第 2 层）**

```
                   Tool Registry
                   ┌──────────────────────────────────────┐
                   │  get_ipo_basic_info       (006a)     │
                   │  get_financial_statements (006a)     │
                   │  get_peer_comparison      (006b 本 PR)│
                   │  get_sentiment_summary    (006b 本 PR)│
                   │  get_historical_winning_rate (006b)  │
                   │  hybrid_search            (006b)     │
                   └──────────────────────────────────────┘
                        ▲
                        │ tools/__init__.py side effect import
                        │ (BE-S2-007 LangGraph 主循环 import 即注册)
                        │
   ┌────────────────────┼────────────────────┐
   │                    │                    │
   ▼                    ▼                    ▼
peers.py           historical.py       hybrid_search.py
(get_session_      (raw SQL 走         (deps 注入 session;
 factory 起        extra->> JSONB     未传时 get_session_
 临时 session)     聚合)              factory 起临时)
                        │
                        ▼
                   sentiment.py
                   (纯占位, 无 IO)
```

**关键设计决策**

1. **peers 走"已上市同行业新股"而非"行业指数 PE"**：spec/04 §3.1 原意"找最近上市的可比新股"，IPO 表本身就是真相；AKShare 行业指数 PE 是 Sprint 3 接入 baseline 的事，本 Tool 接口不变实现层切就行
2. **peers `industry_l2` 优先 + `industry_l1` fallback**：l2 更精细但样本少，单纯走 l2 容易空；fallback 兼容 hkex / akshare 两端 ingest 时主辅分类倒置（也匹配 `industry_l1 == target.industry_l1 OR industry_l2 == target.industry_l1`）
3. **peers `dimensions` 走 `Literal["PE","PB","ROE","GrossMargin","Revenue"]`**：与 spec/04 §3.1 严格对齐；新增维度先改 spec 再改 Tool，防止 LLM 通过 prompt 注入未支持的维度名
4. **sentiment 走 placeholder（不报 fail）**：`ok=True` + `data_source_status="not_connected"` + warning，让 LLM 知道是"数据源未接入"而非"调用失败"，可以选择走 `hybrid_search` 在招股书"风险因素 / 行业前景"章节兜底；接入 BE-S3 文章源后只换 _run 实现不动 schema
5. **historical `one_lot_winning_rate` 从 `extra` JSONB 提**：`IPO` ORM 没有这一列（schema 设计时藏在 extra），走 PG `extra->>'one_lot_winning_rate'`+`::numeric` cast 聚合；与 BE-007 `ipo_service` / `favorite_service` 对该字段的读路径一致（避免 schema 演进）
6. **historical sponsor 过滤走 `sponsors @> '["保荐人"]'::jsonb`**：JSONB 数组成员匹配是 PG 原生操作符 `@>`，用 `json.dumps([sponsor], ensure_ascii=False)` 安全转义防 SQL 注入；ORM 没现成 helper 走 raw `text(...)` + `bindparam` 同 BE-S2-005 风格
7. **historical `first_day_performance: null` 显式留口**：spec 原文有"首日表现统计"但 IPO 表没有 `first_day_close` 字段（K 线源未接），返回 null + note 让 LLM 知道字段存在但 BE-S3 才有
8. **hybrid_search 走"deps 注入 + factory fallback"**：BE-S2-007 LangGraph 主循环已经持有 session 时直接 `runner(args, session=...)`；单测 / 默认时内部走 `get_session_factory()` 起临时 session 不强依赖主循环（解耦层级）
9. **hybrid_search 入参收窄到 5 个语义参数**：BE-S2-005 `hybrid_search` 函数有 9 个调优参数（rrf_k / rerank_pool / ...），LLM 不需要也不应该看到；只暴露 `query / ipo_code / doc_type / lang / top_k`
10. **整套 Tool 入参 `code` 走 `upper().strip()` 归一**：LLM 可能输出 `0700.hk` / `  0700.HK ` 等变体；与 `ipo_service` / `favorite_service` 对 code 的归一约定一致
11. **`patch_session_factory` 集中扩展（不让单测自己 patch）**：测试时 ORM 模块 import 后 `get_session_factory` 已经拷到模块 namespace，必须 patch 到 module 而非 `app.db.base`；统一在 conftest 一处加目标 module 让以后再加 Tool 不用每次复制 monkeypatch

**测试矩阵（38 条新增）**

| 测试文件 | 用例数 | 覆盖 |
|---------|-------|------|
| `tests/test_sentiment_tool.py` | 7 | placeholder 形状 / code upper+strip / window_days 默认 7 / 自定义 / 缺 code / window 太小 / window 太大 / OpenAI schema |
| `tests/test_hybrid_search_tool.py` | 9 | 显式 session 透传（含 ipo_code 归一 + final_top_k 映射 + UUID→str 序列化）/ 不传 session 走 factory / 空结果 warning / lang+doc_type 透传 / 缺 query / 空 query / top_k 越界 / 上游 RuntimeError 归一 / OpenAI schema 含 title 剔除 |
| `tests/integration/test_peers_tool.py` | 9 | l2 优先排序+排除自己（DESC 取最新）/ l1 fallback 凑齐 limit / 该行业第一只 → 空 peers + warning / target 不存在 → failure / 自定义 dimensions 子集 / Literal 拒收非法 dim / code 长度校验 / limit 边界（0 / 999）/ OpenAI schema |
| `tests/integration/test_historical_tool.py` | 13 | industry 过滤（含 NULL 行业排除）/ industry 同时匹配 l1 + l2 / sponsor jsonb @> 过滤 / year_range 单年 / year_range 闭区间 / 全市场无过滤 / 命中 0 warning / 全 NULL warning / status≠listed 排除 / year_range 长度 3 拒收 / start>end 拒收 / 越界 1800 拒收 / OpenAI schema |

**实施成果（2026-04-26 落地）**

| 维度 | Before BE-S2-006b | After BE-S2-006b |
|------|------|------|
| pytest | 373 passed | **411 passed** (+38) |
| ruff | 46 errors | 44 errors（持平 / 略减，BE-S2-006b 触动文件 0 增量） |
| mypy | 23 errors | 23 errors（持平，BE-S2-006b 触动文件 0 增量） |
| 新建文件 | — | `tools/peers.py` + `tools/sentiment.py` + `tools/historical.py` + `tools/hybrid_search.py` 4 src 文件 + 4 测试文件 |
| 已注册 Tool 数 | 2 (a 子任务) | **6** (basic_info / financial / peers / sentiment_placeholder / historical / hybrid_search) |
| Alembic head | 0004_fts | 0004_fts（BE-S2-006b 不动 schema） |

**关键 bug & 修复（PR 内自查发现）**

1. **`one_lot_winning_rate` 不在 `IPO` ORM 列里** — 先按"直接列"写 SQLAlchemy 聚合，mypy 直接 attr-defined error；查阅 BE-007 schema 才发现是 `extra.one_lot_winning_rate` JSONB；改走 raw SQL `extra->>...::numeric` 与 `ipo_service` / `favorite_service` 现有读路径对齐
2. **`IPO.code.notin_(set | True)` mypy 不通过** — set 可能为空时短路 `True` 不是 `ColumnElement`；analyze 后 set 至少含 target code 不会空，直接去掉短路
3. **mypy 推断 `binds` 元素类型** — 第一个 `bindparam(type_=String())` 让 binds 被推为 `list[BindParameter[str]]`，后续 append `Integer` 时类型不兼容；显式 `binds: list[Any] = []` 解决
4. **集成测试 fixture 调用 `get_session_factory()` 拉到生产 DSN** — peers / historical / hybrid_search 三个 Tool module import 时把 `get_session_factory` 拷到自己 namespace 了；patch `app.db.base` 不影响子 module 的 local 引用；统一在 `tests/integration/conftest.py::patch_session_factory` 的 targets 列表追加这 3 个新模块（与 BE-S2-006a `ipo_service_mod` 同思路）

### BE-S2-007 实施成果（2026-04-26 落地）

**改动文件**

| 文件 | 类型 | 说明 |
|------|------|------|
| `apps/api/app/core/config.py` | 修改 | 追加 4 个 Agent 主循环配置：`agent_max_steps=5` / `agent_max_tool_calls_per_step=4` / `agent_decision_temperature=0.0` / `agent_max_tokens_per_step=1500`（防 LLM tool_call 死循环 + tool 放大攻击 + 决策步零温度） |
| `apps/api/app/services/agent/citation.py` | 新建 165 行 | `Citation` / `CitationBundle` dataclass + `build_citations`（chunk_id 去重 + 1-based [n] 编号 + 200 字 snippet 截断 + score float cast） + `validate_citations_in_text`（剔除越界 [n] 占位）+ `assemble` 入口 |
| `apps/api/app/schemas/chat.py` | 新建 105 行 | `ChatDiagnoseRequest` 入参（query / session_id / ipo_code / temperature override）+ 6 个 SSE 事件 payload schema（Start / Delta / ToolCall / Sources / End / Error）+ `ChatCitation` / `ChatTokenUsageDTO` 复用 DTO |
| `apps/api/app/services/agent/system_prompt.py` | 新建 95 行 | `build_system_prompt(ipo_code=...)`：静态 9 条合规红线（CRS 中性 / 无强行投资建议 / 引用必须来自 [n] / 数据缺失明示）+ 动态嵌入 `tool_registry.list_all()` 的 6 个 tool 描述 + 可选会话锚点 IPO code |
| `apps/api/app/services/agent/persistence.py` | 新建 250 行 | 4 张表的薄包装：`get_or_create_session`（resume / new + 64 字 title 截断 + bogus session_id 兜底新建）+ `insert_user/assistant/tool_role_message` + `insert_tool_call_pending` + `finalize_tool_call`（4KB error_message 截断 + status ok/error 流转 + latency 写回）+ `insert_token_usage`（Decimal 成本透传）+ `session_history_to_messages` （tool role 不回放给 LLM） |
| `apps/api/app/services/agent/graph.py` | 新建 410 行 | ReAct 主循环：`StartedEvent` / `TokenDeltaEvent` / `ToolCallEvent` / `FinalAnswerEvent` / `StepErrorEvent` 5 类事件；`run()` async 生成器 N 步循环（plan: `astream_chat_with_meta` 流式拉 delta + tool_calls + usage; act: `_dispatch_tool_calls` 并行 sandbox 调 tool 并落 `chat_tool_calls`; reflect: tool result 喂回 LLM 再循环）；`forbidden_pattern_filter` + `ensure_disclaimer` 收尾；token usage 聚合后 `insert_token_usage`；buffered delta 仅在终步无 tool_call 时回放（中间步 thought 直接丢） |
| `apps/api/app/api/v1/chat.py` | 新建 195 行 | `POST /v1/chat/diagnose` SSE 端层：解析 `ChatDiagnoseRequest` + `get_optional_user`（匿名也可调）+ `get_or_create_session` + `insert_user_message` → `agent_graph.run()` 流转 → `_sse(event_type, payload)` 把 5 类 `AgentEvent` 翻译成 SSE event/data 帧 + 引用源装配后单帧 `sources` 事件 + 每个回答末尾自动 append DISCLAIMER + 错误分支不忘 commit 审计行（事务管理 + try/except + finally rollback） |
| `apps/api/app/api/v1/__init__.py` | 修改 | 注册新路由 `chat.router` |
| `apps/api/tests/test_agent_citation.py` | 新建 14 测 | build_citations 顺序+去重+空 chunk_id 跳过+score float cast+长 snippet 截断+短 snippet 不带省略号 / validate 保留有效 [n] / 剔除越界 / 空文本 / 无 [n] 直通 / 全部越界全剔 / assemble happy / assemble 空 / Citation.to_dict 字段集 |
| `apps/api/tests/test_agent_system_prompt.py` | 新建 5 测 | 红线全在 / 注册 tool 全在 / IPO 锚点段落 / 无 IPO 锚点段落不在 / [n] 引用约定 |
| `apps/api/tests/test_agent_graph.py` | 新建 18 测 | `_result_preview` None / 长字符串 / list+dict 压缩 / dict 大量 key 截断；`_serialize_tool_result_for_llm` ok / failure / 不可序列化 fallback；`_aggregate_usage` 空 / 累加；`_resolve_provider` siliconflow / deepseek_native / zhipu / unknown→siliconflow；5 个 AgentEvent dataclass frozen 形状 |
| `apps/api/tests/integration/test_agent_persistence.py` | 新建 12 测 | 新建会话 / resume 现有 / 64 字截断 / bogus session_id 兜底新建 / user+assistant 落表 / tool role openai_tool_call_id 留存 / list_session_messages 时序 / session_history_to_messages 跳过 tool role / pending→ok 流转 / 4KB error_message 截断 / Decimal cost 透传 / 级联删除 chat_session 清子表 |
| `apps/api/tests/integration/test_chat_diagnose.py` | 新建 5 测 | 无 tool 直接终答 happy / `get_ipo_basic_info` tool call → 终答 / `LLMProviderError` 友好 SSE error / 续聊 history 注入 / `hybrid_search` tool 调用后引用源装配落帧 |
| `spec/09-sprint-2-backlog.md` | 状态 + 实施成果 + 下一步 | 记录 BE-S2-007 落地, 下一步指向 BE-S2-008 |

**架构（Tool Use 第 4 层 - ReAct 主循环）**

```
                                   POST /v1/chat/diagnose (SSE)
                                          │
                  ┌───────────────────────┴────────────────────────┐
                  │                                                │
              端层 chat.py                                  agent_graph.run()
              ─────────────                                 ─────────────────
                  │
   ┌──────────────┼──────────────┐
   │              │              │
   ▼              ▼              ▼
get_optional   persistence.    _sse(event,
_user (匿       get_or_create  payload) → SSE
名也可调)        _session       帧装配
                                                   ┌────────────────────────────┐
                                                   │  ReAct N 步 (max=5)         │
                                                   │  ┌──────────────────────┐  │
                                                   │  │ plan:                │  │
                                                   │  │  astream_chat_with   │  │
                                                   │  │  _meta + tools schema│  │
                                                   │  │  → delta / tool_calls│  │
                                                   │  │   / usage            │  │
                                                   │  └────────┬─────────────┘  │
                                                   │           │                │
                                                   │  has tool_calls?           │
                                                   │     │           │          │
                                                   │     ▼ yes       ▼ no       │
                                                   │  act: parallel  yield      │
                                                   │  sandbox 调 tool buffered  │
                                                   │  → ToolResult   delta      │
                                                   │  → tool role    + Final    │
                                                   │  msg 喂回 LLM   AnswerEvent│
                                                   │     │                      │
                                                   │     └─→ 下一步              │
                                                   └────────────────────────────┘
                                                              │
                                                              ▼
                                          insert_token_usage + ensure_disclaimer
                                                              │
                                                              ▼
                                          assemble citations from hybrid_search
                                                              │
                                                              ▼
                                       SSE: start → delta* → tool_call* → sources → end
```

**关键设计决策**

1. **不引 LangGraph 库, 自实现 ReAct 主循环**：spec/04 §3.2 给的"plan / act / reflect"3 节点是固定 graph 不会变, LangGraph 的 StateGraph + Channel 抽象对当前需求是过度工程；自实现 410 行 `graph.py` 反而看得清主循环边界 + 不引入二级依赖。后期需要分支决策（如 router pattern）再切 LangGraph，接口不变
2. **buffered delta + 终步回放**：ReAct 主循环里 LLM 中间步 plan 可能也输出文字（"我先查一下基本面..."），但这是 LLM 内部 reasoning, 不该流到用户 UI。改成全程 buffer delta, 只在该步无 tool_calls（即"终步"）时回放为 `TokenDeltaEvent`；中间步直接丢, 让前端只看到"工具调用 + 最终回答"的清爽流
3. **DB 事务边界放在端层**：`AsyncSession` 在 `chat.py::chat_diagnose` 起, 一路传给 `agent_graph.run` → `persistence.*`；整次请求（用户 msg + N 步 LLM + N 个 tool_call + 终步 assistant + N 条 token_usage）= 1 个事务。错误分支也走 `try/except` 把已经 emit 的审计行 commit 掉（运维查问题需要看到"在哪一步炸的"）
4. **tool result 序列化在 graph 层做**：`_serialize_tool_result_for_llm` 把 `ToolResult.data` json.dumps（fallback 到 str repr）；不在 Tool sandbox 内做, 因为 sandbox 不需要知道 LLM 协议；所有"喂回 LLM 的字符串"集中在 graph.py 一处, 改成 yaml / xml format 时只动一行
5. **citation 只对 `hybrid_search` 工具结果做**：spec/04 §3.3 C 项严格要求"引用必须来自检索结果", 其他 tool 返回的是结构化数据（PE / 同业 ROE / 招股书页码）走 LLM 自然语言提及；citation pipeline 只扫 graph 全部步里 `hybrid_search` 的 result, 装成 1-based 数组喂 SSE `sources` 帧
6. **匿名也可调 `/v1/chat/diagnose`**：依赖 `get_optional_user`（不强制 JWT）, 让"未注册用户"也能 trial 一轮（BE-S2-008 配额会再加"匿名 = IP 限流 / 注册 = 用户限流"分支）；匿名时 `chat_session.user_id=NULL`, 续聊靠 session_id 维持
7. **system prompt 分两段**：静态 `_BASE`（9 条合规红线 + 输出格式 + 引用约定）+ 动态 `_format_tool_catalog()`（注册中心 list_all 转人类可读）；新加 Tool 自动出现在 prompt 里, 不用手动同步两处
8. **DISCLAIMER 自动追加**：`ensure_disclaimer` 在终步 LLM 输出后做"未带 DISCLAIMER 字符串就 append"；不靠 LLM 自己加（LLM 偶尔会忘 + 偶尔会篡改），端层硬上一道闸门
9. **`forbidden_pattern_filter` 端层兜底**：哪怕 system prompt + LLM 双层都失守, 端层走完整字符串过滤"必涨 / 稳赚 / 内部消息"等违规词 → 替换为 ★, 再 SSE 出去；与 BE-S2-002 LLM facade `forbidden_pattern_filter` 共用同一份正则词表
10. **`bogus session_id` 不抛 404 而兜底新建**：客户端可能本地缓存了过期的 session_id（DB 已被运营清理）, 强 404 会让用户体验差；改成"找不到就静默新建"+ 返回真 session_id 给前端覆写本地缓存。同侧, title 64 字符截断 + error_message 4KB 截断都是防 LLM 输出超长撑爆列宽
11. **SSE 帧最简且自描述**：每帧 `event:` + `data:` 双行, payload 严格按 `ChatStartPayload` / `ChatDeltaPayload` / ... 6 个 schema；前端解析时按 `event` 字段 dispatch 不用脑补 union 类型

**测试矩阵（54 条新增, 累计 465 passed）**

| 测试文件 | 用例数 | 覆盖 |
|---------|-------|------|
| `tests/test_agent_citation.py` | 14 | build_citations 顺序 / chunk_id 去重 / 空 chunk_id 跳过 / score float cast / 长 snippet 截断 / 短文本不带省略号 / validate 保留有效 / 剔除越界 / 空文本 / 无 [n] 直通 / 全越界 / assemble happy / 无结果 assemble / Citation.to_dict 字段集 |
| `tests/test_agent_system_prompt.py` | 5 | 红线全在 / 注册 tool 全在 / IPO 锚点段落 / 无 IPO 锚点段落不在 / [n] 引用约定 |
| `tests/test_agent_graph.py` | 18 | _result_preview 4 形 / _serialize_tool_result_for_llm 3 形 / _aggregate_usage 2 形 / _resolve_provider 4 形 / 5 AgentEvent dataclass frozen 形 |
| `tests/integration/test_agent_persistence.py` | 12 | session 新建 / resume / title 截断 / bogus 兜底 / user+assistant 落表 / tool role openai_tool_call_id 留存 / 时序 / session_history 跳 tool role / pending→ok / 4KB error 截断 / Decimal cost / 级联删 |
| `tests/integration/test_chat_diagnose.py` | 5 | 无 tool 直接终答 / get_ipo_basic_info tool call → 终答 / LLMProviderError 友好 SSE / 续聊 history 注入 / hybrid_search → 引用源装配 |

**实施成果（2026-04-26 落地）**

| 维度 | Before BE-S2-007 | After BE-S2-007 |
|------|------|------|
| pytest | 411 passed | **465 passed** (+54) |
| ruff | 44 errors | 44 errors（持平 / B008 在 chat.py 复刻全项目 FastAPI 路由风格债） |
| mypy | 23 errors | 23 errors（持平，BE-S2-007 触动文件 0 增量） |
| 新建文件 | — | `services/agent/citation.py` + `services/agent/system_prompt.py` + `services/agent/persistence.py` + `services/agent/graph.py` + `schemas/chat.py` + `api/v1/chat.py` 6 src 文件 + 5 测试文件 |
| 已注册 Tool 数 | 6 | 6（BE-S2-007 不动 Tool 层） |
| Alembic head | 0004_fts | 0004_fts（BE-S2-007 不动 schema） |
| API 路由数 | — | +1 (`POST /v1/chat/diagnose` SSE) |

**关键 bug & 修复（PR 内自查发现）**

1. **SSE 帧分隔符 `\r\n\r\n` 而非 `\n\n`** — `sse_starlette` 实际写出的是 `\r\n` 行尾（HTTP 标准），`\r\n\r\n` 块分隔；测试 `_parse_sse` 工具按 `\n\n` 分会拿到一个大块。改成"先 normalize `\r\n` → `\n` 再 split `\n\n`"，前端 EventSource 浏览器侧自带兼容不需改
2. **buffered delta 在中间步丢失** — 初版 `graph.py` 走"先 yield delta 再判断有无 tool_calls"流程, 结果中间步 plan 的"我先查一下..."文字会泄漏到用户 UI；改成"先全程 buffer, 终步统一回放"模式（即"middle-step thought = silent" 的 ReAct 经典约定）
3. **`session_history_to_messages` 不能把 tool role 喂回 LLM** — OpenAI tool role message 必须紧跟 assistant tool_call 后面（有 `tool_call_id` 配对）, 续聊时单独再喂会触发 400。`session_history_to_messages` 显式过滤 role=tool（保留 user / assistant）
4. **Decimal `cost_cny=0` 不能 None** — `chat_token_usage.cost_cny` NOT NULL；价格表 fallback 时 `_PRICE_CNY_PER_M_TOKENS.get(model, Decimal('0'))` 必须返回 `Decimal('0')` 不是 None；BE-S2-002 facade 已经做了这一层, BE-S2-007 直接复用 `TokenUsage.cost_cny: Decimal`
5. **`bogus session_id` 抛 NoResultFound** — 用户客户端可能本地缓存了过期 session, `select(...).one()` 直接 NoResultFound 撑爆 SSE 流；改成 `select(...).one_or_none()` + None 时 fallthrough 走"新建"路径

### BE-S2-008 ✅ 实施成果（Agent 配额管理: 滑动窗口 5/天 + VIP 无限 + 匿名 IP 限流）

**改动文件**

| 文件 | 行数 | 说明 |
|------|------|------|
| `app/cache/redis_client.py` | +180 | 给 `RedisClientProtocol` 加 3 个高层滑动窗口接口 (`sliding_window_count` / `sliding_window_record` / `sliding_window_oldest_ms`)，RealRedisClient 走 Lua 原子脚本 (`ZREMRANGEBYSCORE` + `ZADD` + `EXPIRE` + `ZCARD` 一脚本完成), InMemoryRedisClient 走排序 list + asyncio.Lock 等价语义 |
| `app/services/agent/quota.py` (NEW, 290 行) | +290 | 核心模块: `QuotaPlan` (StrEnum) / `QuotaStatus` / `QuotaExceeded` / `check_quota` / `record_usage` / `resolve_plan` |
| `app/core/config.py` | +50 | 4 个 quota 设置 (`agent_quota_window_seconds=86400` / `free_per_window=5` / `anonymous_per_window=2` / `vip_per_window=-1`) + `vip_user_id_whitelist` CSV |
| `app/api/v1/chat.py` | +90 | SSE 入口前置闸门 (`HTTPException(429, ChatQuotaExceededResponse)`) + `record_usage` (在 `user_message` 写库后立即扣额, fail-open) + race 兜底 (record 时被并发挤超也走 SSE error) + `_resolve_client_ip` 拼匿名 quota key |
| `app/schemas/chat.py` | +35 | `ChatQuotaPayload` + `ChatQuotaExceededResponse` (HTTP 429 body, OpenAPI 自动收录) |
| `tests/test_sliding_window.py` (NEW, 12 tests) | +185 | InMemoryRedisClient 滑动窗口 3 个接口的不变量: 同 member 不重复 / 不同 member 累加 / 出窗清旧 / oldest_ms 边界 / count 只读 / key 隔离 |
| `tests/test_agent_quota.py` (NEW, 18 tests) | +355 | `resolve_plan` (匿名/FREE/VIP CSV 大小写) + `QuotaStatus.has_quota / to_dict` + `check_quota` (VIP 跳过 Redis / FREE 不消费 / 匿名分支) + `record_usage` (累加 / 超额抛 / user 隔离 / 匿名 IP 隔离 / 默认 uuid member 不停滞) + `retry_after` 边界 |
| `tests/integration/test_chat_diagnose_quota.py` (NEW, 5 tests) | +355 | E2E: 匿名超额 → 429 (含 retry-after header) / 429 时 user_message 不落 DB / FREE 超额 → 429 + plan=free / 多用户 quota key 隔离 / VIP 不限流 (3 次都 200) |

**架构（配额闸门 - 第 5 层 - 进流前 + 进流后双扣）**

```
┌────────────────── POST /v1/chat/diagnose ──────────────────┐
│                                                            │
│  ① get_optional_user (匿名 / 登录都行)                       │
│  ② _resolve_client_ip (X-F-F 第一段 / request.client.host)  │
│                                                            │
│  ③ check_quota(user, anon_key) ────────► Redis ZSET 只读
│        │                                  (sliding_window_count)
│        ▼                                  返当前用量
│   has_quota?                                              
│      ├── False → HTTPException(429, ChatQuotaExceededResponse)
│      │                + Retry-After header
│      │           (FE 拿到 status code 弹升级 modal)
│      └── True → 进 SSE 流                                   
│                                                            
│  ④ session / user_message 写 DB
│                                                            │
│  ⑤ record_usage(user, anon_key, member=msg_id) ──► Redis ZSET 写入
│        │                                  (sliding_window_record:
│        ▼                                   ZREMRANGEBYSCORE +
│      QuotaExceeded? (race: check 通过但写时被并发挤超)        ZADD + EXPIRE + ZCARD)
│      ├── True → SSE event=error + end (ok=false, quota_exceeded=true)
│      └── False → 继续走 graph.run 主循环                     
│                                                            
│  ⑥ async for evt in agent_graph.run(...) → SSE              
│                                                            
│  ⑦ commit DB (audit log 保留, 即使 LLM 失败也持久化)          │
└────────────────────────────────────────────────────────────┘
```

**关键设计决策**

1. **滑动窗口 (24h ZSET) 而非固定窗口 (INCR + EXPIRE)**：固定窗口边界突发能让用户在窗口切片瞬间拿到 2× 配额（00:59 跑 5 次, 01:00 又能 5 次 = 1 分钟 10 次）, 滑动窗口是"过去 24h 内不超过 5 次"的精确语义；spec/04 §限流原文要求"滑动窗口"
2. **Lua 原子脚本**：`ZREMRANGEBYSCORE` 清旧 → `ZADD` 写新 → `EXPIRE` → `ZCARD` 单脚本一次 RTT 完成，原子，避免"先读 ZCARD 再 ZADD"中间窗口被清的 race
3. **check 与 record 分两步**：进流前 `check_quota` 不计数（DB 异常不应该被错扣额）, 进流后 `user_message` 写 DB 之后立即 `record_usage` 扣额；这样"会话初始化失败"不扣, "LLM 失败但 user_message 已落"扣 1
4. **VIP noop 不写 Redis**：`limit=-1` 时 `record_usage` 直接 return, 节省一次 RTT；Sprint 3 改"VIP 50/天"时只把 settings 改成有限值, 不动接口
5. **匿名走 IP key (`rate:agent:anon:<ip>`)**：`X-Forwarded-For` 第一段为真实 IP（反代场景）, fallback 到 `request.client.host`（直连）；NAT 共享 IP 用户共用一个 key, 安全侧"宁紧勿松"
6. **VIP 走 settings whitelist 兜底, 不引 vip_memberships 表**：`vip_user_id_whitelist` CSV 一行配置, Sprint 3 接订阅表后只换 `_resolve_plan` 函数实现, 接口 + 调用方 0 改动；`csv_whitelist_with_spaces` 测试覆盖了大小写 + 空格
7. **race 容忍**：`check` 与 `record` 两次 RTT 之间极端并发可能 1~2 次溢出, 日均 5 次低频场景可接受；`record_usage` 内部仍判 `used > limit` 抛 `QuotaExceeded` 兜底, 端层 SSE error event 保护体验。Sprint 3 极致原子化时换"INCR 后立即检查"
8. **fail-open Redis**：`check_quota` / `record_usage` 异常 → `logger.warning` 不阻塞, 让"Redis 挂导致全平台 Agent 不可用"不发生；代价是 Redis 故障窗口内不限流（运维侧告警比业务限流更重要）
9. **429 body 结构 (`ChatQuotaExceededResponse`)**：`code = "agent_quota_exceeded"` (FE 用 code 判逻辑) + 人话 `message` (FE 默认 toast) + `quota` 详情 (FE 弹升级 modal); `Retry-After` HTTP header 走标准协议, 浏览器自带 backoff 友好
10. **member = user_msg.message_id**：ZSET 同 member 同 score 不增 ZCARD, 用 message_id 保证每次调用有独立 member（uuid4 兜底）, 计数不漂

**测试矩阵** (35 个新测试, 累计 500 个)

| 文件 | 测试数 | 覆盖点 |
|------|--------|--------|
| `tests/test_sliding_window.py` | 12 | InMemoryRedisClient 3 个滑窗 API 不变量 |
| `tests/test_agent_quota.py` | 18 | resolve_plan / QuotaStatus / check_quota / record_usage / retry_after |
| `tests/integration/test_chat_diagnose_quota.py` | 5 | 匿名 429 / DB 不落 / FREE 429 / 多用户隔离 / VIP 不限 |

**实施成果**

| 指标 | 改前 (BE-S2-007 ✅) | 改后 (BE-S2-008 ✅) |
|------|------|------|
| pytest 通过数 | 465 | **500 (+35)** |
| ruff 增量错误 | 0 | **0** (BE-S2-008 文件自身 0 增量, baseline 50 总数不变换) |
| mypy 增量错误 | 0 (baseline 25) | **0 (baseline 25)** |
| 累计 DB 表 | 11 (含 4 张 chat_*) | 11 (BE-S2-008 不动 schema) |
| API 路由数 | +1 `POST /v1/chat/diagnose` | 同 (添加 429 响应模型, 路径不动) |
| 配置项 | — | +5 (`agent_quota_*` 4 项 + `vip_user_id_whitelist`) |

**关键 bug & 修复（PR 内自查发现）**

1. **`enum.Enum + str` 双继承被 ruff UP042 标红** — Python 3.11+ 应该用 `enum.StrEnum` 标准化封装；改成 `class QuotaPlan(StrEnum)` 后 `json.dumps(plan)` 直接拿字符串值, 不需要 `.value`
2. **`B008` (Depends in argument default)** — chat.py 加 `Request: Request` 参数后又触发 2 个 B008；项目里其他路由 (favorites/invite/me/auth) 都用同样风格, 决定保持惯例不修, 写在"已知 baseline 风格债"里
3. **`fake_streaming_llm` 跨用例脚本未消费**：测试初版让 LLM 提前 push 多个 script, race 用例的第二次调用又复用同一个 fixture, 导致脚本队列错位；改成"每条用例独立 push, 顺序按 LLM 调用次序"
4. **测试 `User` 重名 phone 导致 unique 冲突**：`_seed_user_and_token` 第二次调用 phone+invite_code 默认就重了；接收 `phone_suffix` 参数让多用户测试得以隔离
5. **`Retry-After` header 缺失**：HTTPException 加自定义 header 容易被忽略；测试断言 `r.headers["retry-after"]` 后补上 `headers={"Retry-After": str(retry_after_seconds)}`
6. **匿名 anon_key=None 共享 key 引发 cross-user 测试串扰**：单测 `test_record_anonymous_unknown_ip_uses_fallback` 验证"None 走 'unknown' 兜底"是有意行为（安全侧）, 但写文档说明; 集成测的 `client` fixture 走 ASGITransport 的 `request.client.host = 'testclient'`, 多用例间 IP 相同, 走 InMemory 隔离不会跨 fixture 串扰

→ **建议下一步走 BE-S2-009**（评测集 80 条 + 离线评测脚手架: spec Agent 主线 BE-S2-008 ✅ → **BE-S2-009** → FE-S2-001 / QA-S2-001。BE-S2-009 PR 内会落地: `eval/dataset/sprint2_80q.jsonl`（80 条标注 query, 覆盖 IPO 基本面 / 风险 / 对标 / RAG 召回 4 类）+ `eval/run_eval.py`（离线评测脚手架: 召回@5 / 幻觉率 / LLM-as-judge 三指标）+ `Makefile eval-sprint2` + 报告 markdown）

## ✅ Sprint 2 完成后的产出物

- 用户在对话页可以多轮追问任意一只新股（招股书 RAG 引用源可点开看）
- AI 输出在硬护栏（关键词过滤 + 强制免责声明 + 引用强制校验）下基本不出违规
- 评测集 80 条 → 召回@5 ≥ 0.7（baseline）/ 幻觉率 ≤ 10% / 单次平均成本 < ¥0.05
- 配额 5 次/天 / VIP 无限的限流闭环跑通，前端有友好升级引导
- HK IPO 走真源 ingest（hkexnews）入库，招股书 URL 闭环到 RAG 流水线
- 16 PR + 累计 ≥ 250 个测试 + 11 张 DB 表 + 1 个 LangGraph + 80 条评测集

> 然后进入 Sprint 3（文章聚合 + 券商对比 + VIP 订阅），spec/07 §S3 拆任务时再开新 backlog 文档。
