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
| **BE-S2-006a** (Tool 注册中心 + 2 个最简工具 basic_info / financial, 1d) | RAG 主线 BE-S2-000 ✅ → BE-S2-004 ✅ → BE-S2-005 ✅ 全部落地；现在切到 Tool Use 主线。BE-S2-006a 只依赖 BE-S2-001（chat_tool_calls 表），可立即起；BE-S2-007 LangGraph 主循环要先有 Tool 注册中心 + hybrid_search Tool 才能跑 ReAct 循环 |
| BE-S2-006b (余下 3 Tool: peers / sentiment / historical, 1d) | 依赖 BE-S2-006a 的 Tool 注册基础设施 |
| BE-S2-007 (LangGraph 主循环 + 引用源装配, 1.5d) | 三依赖齐: BE-S2-002 facade ✅ + BE-S2-005 hybrid_search ✅ + BE-S2-006b 5 Tool。BE-S2-005 已让 hybrid_search 直接可作为 Tool 注入 |

→ **建议下一步走 BE-S2-006a**（Tool 注册中心 + 2 工具：spec RAG 主线 BE-S2-000 ✅ → BE-S2-004 ✅ → BE-S2-005 ✅ → **BE-S2-006a** → BE-S2-006b → BE-S2-007。BE-S2-006a PR 内会落地：`app/services/agent/tool_registry.py` 注册中心（OpenAI tool schema 协议）+ `basic_info` / `financial` 两个对接 BE-007 现有 IPO 表的最简工具 + 沙盒（超时 / 异常归一）+ 单测）

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

## ✅ Sprint 2 完成后的产出物

- 用户在对话页可以多轮追问任意一只新股（招股书 RAG 引用源可点开看）
- AI 输出在硬护栏（关键词过滤 + 强制免责声明 + 引用强制校验）下基本不出违规
- 评测集 80 条 → 召回@5 ≥ 0.7（baseline）/ 幻觉率 ≤ 10% / 单次平均成本 < ¥0.05
- 配额 5 次/天 / VIP 无限的限流闭环跑通，前端有友好升级引导
- HK IPO 走真源 ingest（hkexnews）入库，招股书 URL 闭环到 RAG 流水线
- 16 PR + 累计 ≥ 250 个测试 + 11 张 DB 表 + 1 个 LangGraph + 80 条评测集

> 然后进入 Sprint 3（文章聚合 + 券商对比 + VIP 订阅），spec/07 §S3 拆任务时再开新 backlog 文档。
