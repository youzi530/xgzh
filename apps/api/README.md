# xgzh-api

XGZH (新股智汇) FastAPI 后端。

## 当前能力（Sprint 0 + INFRA-001/002 + BE-001/002/003/004/005/006/007/008/009/010/011）

API:

- `GET /healthz` 健康检查
- `GET /api/v1/ipos?market=A&status=listed&industry=信息技术&page=1&size=20` A 股新股列表（走 `ipos` 表，BE-007 调度入库；`status` / `industry_l1` 精确筛选；分页 size 1-100；`listing_date DESC NULLS LAST` 排序；Redis 缓存 10min，BE-008）
- `GET /api/v1/ipos?market=HK` 港股新股列表（akshare 暂用 seed，HKEX/Futu 接入排 Sprint 2；同 schema 支持 status/industry/page/size）
- `GET /api/v1/ipos/{code}` 新股详情（BE-009：返回 `IPODetail`，含 `sponsors` / `underwriters` / `prospectus_url` / `highlights` / `risks` / `financial_summary`；A/US 走 DB，HK 走 seed；`@cached(ttl=1800s, namespace="ipos:detail")` 30min；`extra` JSONB 自动提取顶层字段；404 标准化错误码 `ipo_not_found`）
- `POST /api/v1/agent/diagnose` AI 一键诊断（DeepSeek-V3 SSE 流式）
- `POST /api/v1/auth/otp/send` 手机号 OTP 发送（dev 走 Mock SMS，60s 限流，5min TTL）
- `POST /api/v1/auth/login/phone` OTP 校验 + 自动注册 + 颁发 access/refresh JWT（5/5min 限流）
- `POST /api/v1/auth/login/wechat-mp` 微信小程序 code → openid/unionid → 注册/登录 + JWT（同 code 5/min 限流）
- `POST /api/v1/auth/refresh` Refresh token rotation：旧 refresh 拉黑 + 颁发新 access+refresh（5/min 限流）
- `POST /api/v1/auth/logout` 拉黑当前 access（+ 可选拉黑 refresh），需 `Authorization`
- `GET /api/v1/me` 当前用户基本信息（需 `Authorization: Bearer <access_token>`）
- `POST /api/v1/invite/bind` 绑定邀请人（一次性，需登录，10/min/user 限流）
- `POST /api/v1/favorites` 添加自选（BE-010：幂等，需登录；body `{code, notify_on_subscribe?}`）
- `DELETE /api/v1/favorites/{code}` 移除自选（幂等：不存在也 200 + `removed=False`）
- `GET /api/v1/favorites` 当前用户全部自选（LEFT JOIN `ipos` 带最新行情字段；按 `favorited_at DESC` 排）
- `POST /api/v1/push/tokens` 注册推送 token（BE-011：幂等覆盖，需登录；body `{platform, token, device_id}`；响应**不回显 token**）
- `DELETE /api/v1/push/tokens?platform=&device_id=` 注销推送 token（幂等：不存在也 200 + `removed=False`）

后台调度（`app/scheduler/__init__.py` + `app/services/ipo_ingest_service.py`，BE-007）:

- 进程内 `AsyncIOScheduler`（APScheduler 3.x），FastAPI lifespan 启动 + finally `wait=False` 优雅关闭
- 启动后 `IPO_INGEST_INITIAL_DELAY_SECONDS` 秒触发一次 A 股 IPO 抓取（兜底，避免重启后 12h 没数据）
- 每天 cron `IPO_INGEST_CRON_HOURS` 整点（默认 `8,20`，时区 `Asia/Shanghai`）跑全量更新
- `coalesce=True` + `max_instances=1`：错过的多次执行只补跑一次，且不会并发跑两个
- `run_ingest_a_job` 永不抛：fetch / parse / DB 任何异常都 `logger.exception` 后返回 `{"errors": 1}`
- `upsert_ipos` 走 PG `ON CONFLICT (code, market) DO UPDATE` 一条 SQL，200 行 < 100ms
- `COALESCE(EXCLUDED.x, ipos.x)` 兜底：`industry / pe_ratio / issue_price` 等新值为 NULL 时不擦旧值
- `name / extra / updated_at` 强制覆盖
- 多副本：K8s 上 web pod 关 `SCHEDULER_ENABLED=false`，单独跑一个 worker pod 开
- HK 仍走 seed（`fetch_hk_ipos` 留 TODO，Sprint 2 接 HKEX/Futu 后启用 `run_ingest_hk_job`）

```bash
# 启动 web (lifespan 自动拉起 scheduler)
uv run uvicorn app.main:app --reload --port 8000
# 期望日志: scheduler.jobs_registered ... scheduler.started

# 一次性手动跑 (CI / 开发本地灌种子用)
uv run python -c "
import asyncio
from app.services import ipo_ingest_service
print(asyncio.run(ipo_ingest_service.run_ingest_a_job()))
"
# → received=200 inserted=200 updated=0
# 二次跑 → received=200 inserted=0 updated=200 (upsert 语义)
```

Schema（Alembic `0001_init` + `0002_chat` + `0003_chunks`，PG 14 + pgvector 0.8.2）:

- Sprint 1 (`0001_init`)：`users` / `auth_sessions` / `invite_codes` / `ipos` / `ipo_documents`（embedding `vector(1024)` + HNSW 索引）/ `user_favorites` / `push_tokens`
- Sprint 2 (`0002_chat`, BE-S2-001)：`chat_sessions` / `chat_messages` / `chat_tool_calls` / `chat_token_usage`
  - 4 张表是 LangGraph + Tool Use 全链路的底座，详细规范见 `app/db/models/chat.py` docstring
  - 6 个二级索引：`(user_id, created_at)` / `(ipo_code, created_at)` / `(session_id, created_at)` / `(tool_name, created_at)` / `(model, created_at)` / `(created_at)`
  - 级联：会话→消息→工具调用/Token 用量 ON DELETE CASCADE；用户删除则会话 user_id SET NULL（与 invite_codes 同策略）
  - append-only：`chat_messages` / `chat_tool_calls` / `chat_token_usage` 不带 `updated_at`（写入即历史，防 LLM 输出篡改）
- Sprint 2 (`0003_chunks`, BE-S2-003)：扩展 `ipo_documents` 让 RAG 入库流水线（BE-S2-004）和混合检索（BE-S2-005）有米下锅
  - 新增 6 列：`chunk_index`（同 doc 内顺序）/ `token_count`（cost 调试）/ `content_hash CHAR(64)`（sha256 防重）/ `embedding_model`（默认 `BAAI/bge-m3`，多版本共存）/ `embedding_dim`（默认 1024）/ `lang`（`zh`/`en`）
  - 新增 2 partial 索引：`uq_ipo_documents_doc_id_content_hash`（UNIQUE PARTIAL `WHERE content_hash IS NOT NULL`，BE-S2-004 直接 `ON CONFLICT DO NOTHING`）+ `ix_ipo_documents_doc_id_chunk_index`（取相邻上下文）
  - 老 schema 零变动：HNSW 索引、`(ipo_code, doc_type)` 复合索引、Sprint 1 列全保留；测试桩 `content_hash IS NULL` 的老行不被 partial UNIQUE 卡住
  - 全文检索 / `tsvector` 列 punt 给 BE-S2-005 一条独立 0004 migration（中文分词器选型独立决策）
- Sprint 2 (BE-S2-000，无 schema 改动)：HK IPO ingest 真源接入 — `app/adapters/hkex_client.py` 抓 hkexnews `applicants_c.htm` 列表 + BeautifulSoup 解析（公司名 / 递交日 / 招股书 PDF URL），16 字符占位 code `AP{yymmdd}{slug:5}.HK` 贴 `ipos.code VARCHAR(16)` 上限（不动 schema 避免 user_favorites FK 迁移）
  - `run_ingest_hk_job` 走 `upsert_ipos` 复用 BE-007 写入逻辑；**关键守护**：`extra` 改为 `func.coalesce(cur.extra, '{}'::jsonb).op('||')(excl.extra)` 浅合并（PG `jsonb || jsonb`），防 BE-S2-004 RAG 写入 `highlights` / `risks` 被 ingest 整体覆盖（4 条 regression 测）
  - APScheduler 二刀流：`ipo_ingest_hk_initial`（启动延迟 10s，错开 A 股 5s）+ `ipo_ingest_hk_cron`（默认 `9,17` `Asia/Hong_Kong`，开盘前 + 收盘后），独立 timezone
  - `ipo_service.list_ipos / get_ipo / get_ipo_detail` 切 DB 路径；DB 空表（首次部署 / 无 ingest 测试场景）走 `hkex_client.get_cold_start_seed` 3 条样例兜底，不让首页空白
  - 配置层 7 个新字段：`HKEX_BASE_URL` / `IPO_INGEST_HK_LIMIT` / `IPO_INGEST_HK_CRON_HOURS` / `IPO_INGEST_HK_INITIAL_DELAY_SECONDS` / `IPO_INGEST_HK_TIMEZONE` / `IPO_INGEST_HK_REQUEST_TIMEOUT_SECONDS` / `IPO_INGEST_HK_REQUEST_CONCURRENCY`
- Sprint 2 (BE-S2-004，无 schema 改动)：招股书 PDF 入库流水线 — 新建 3 个文件 `app/adapters/pdf_loader.py` / `app/services/rag/chunker.py` / `app/services/rag/prospectus_ingest_service.py`
  - **下载层**：`pdf_loader.fetch_pdf_bytes(url, max_size_mb, request_timeout)` httpx 流式下载 + `Content-Length` 提前拒 + 累计字节兜底（防对端不诚实），`PDFFetchError` 归一化所有 fetch / extract 错误
  - **解析层**：`extract_text_per_page(pdf_bytes)` pypdf 6.x `PdfReader`；单页失败 logger.warning + skip，全空（扫描版 PDF）抛 `PDFFetchError`；输出 1-based page no 与读者视觉一致
  - **切分层**：`chunker.split_text(text, max_tokens=500, overlap_tokens=50)` 段落（双换行）→ 句子（中英句号）→ 字符 3 层 fallback；`estimate_tokens` 启发式（CJK 1:1，英文 4:1，与 bge-m3 真实 tokenizer 偏差 ±15%，仅用于 cost 调试，不引 tiktoken / transformers 重依赖）
  - **编排层**：`run_ingest_prospectus(session, ipo_code, prospectus_url, lang)` 5 阶段 stats（`pdf_pages` / `extracted_pages` / `chunks_total` / `chunks_embedded` / `inserted` / `skipped_duplicates` / `errors` / `stage`），失败一律 logger.exception + 返回 stats 不抛
  - **幂等键**：`doc_id = sha256(url)[:32]` + `content_hash = sha256(chunk.text)`；`ON CONFLICT (doc_id, content_hash) WHERE content_hash IS NOT NULL DO NOTHING` 走 BE-S2-003 partial UNIQUE 索引（**关键 bug 自查：partial UNIQUE 必须给 `index_where` 谓词**, 否则 `InvalidColumnReferenceError: no constraint matching`）
  - **dim 防污染**：embed 返回维度 ≠ `settings.llm_embedding_dim` 时 stage=embed 拒收，防 vector(1024) 索引被 512 维或异常维向量污染
  - **本 PR 不挂 scheduler**：招股书几十 MB × N 只新股，lifespan startup 自动跑会撑爆带宽 / 临时盘；改成手动入口给 Sprint 3 运营触发面板留口
  - 配置层 4 个新字段：`PDF_MAX_SIZE_MB`（50）/ `PDF_REQUEST_TIMEOUT_SECONDS`（60）/ `RAG_CHUNK_SIZE_TOKENS`（500）/ `RAG_CHUNK_OVERLAP_TOKENS`（50）；新依赖 `pypdf>=5.0`
- Sprint 2 (BE-S2-005)：混合检索（vector + BM25 + RRF + bge-reranker）— Alembic 0004 给 `ipo_documents` 加 `tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', regexp_replace(text, E'([\\u4e00-\\u9fff])', E'\\\\1 ', 'g'))) STORED` + GIN 索引；新建 `app/services/rag/hybrid_search.py`
  - **不上 zhparser**：装难（sudo make + scws 字典） + CI 跑不了 + 字典维护重；用 PG ``simple`` config + 中文字符级预切替代。**单一真相**：写入端 0004 migration 与查询端 `_cjk_presplit` 用同样正则 `[\u4e00-\u9fff]`，偏差 = 0
  - **三阶段架构**：`hybrid_search(session, query, *, ipo_code, doc_type, lang, ...)` ❶ vector 召回 (HNSW cosine `embedding <=> CAST(:q AS vector)` ORDER ASC) ❷ BM25 召回 (`tsv @@ plainto_tsquery('simple', ...) ORDER BY ts_rank_cd DESC`) ❸ Reciprocal Rank Fusion (Cormack 2009, `score = Σ 1/(rrf_k + rank_i)`, k=60) ❹ bge-reranker-v2-m3 cross-encoder 二阶段精排
  - **过滤推 SQL**：`ipo_code` / `doc_type` / `lang` / `embedding_dim = settings.llm_embedding_dim` 全在 WHERE，利用现有 `(ipo_code, doc_type)` btree 索引；多版本 embedding 隔离（将来 bge-m4 共存）
  - **失败链路**：vector 失败 → 仅 BM25；BM25 失败 → 仅 vector；两路都失败 → 抛 `RuntimeError` (上层明确感知); rerank 失败 → fallback RRF 顺序；空 query / 全标点 query / dim mismatch 全有兜底
  - **不在本 PR 做 query rewrite / HyDE / 语义缓存**：那些是 BE-S2-007 LangGraph 主循环里干（有 LLM context 才能改写）；本层只做单 query 检索原语
  - **ORM 不反映 tsv 列**：generated column read-only，BM25 走 raw SQL；ORM INSERT 时字段不在映射 = 不会被写入 = PG 自动按生成表达式填值 = BE-S2-004 业务代码 0 改动
  - 配置层 6 个新字段：`RAG_VECTOR_TOP_K=50` / `RAG_BM25_TOP_K=50` / `RAG_RRF_K=60` / `RAG_RERANK_POOL_SIZE=20` / `RAG_FINAL_TOP_K=5` / `RAG_USE_RERANK=true`
- Sprint 2 (BE-S2-006a)：Tool 注册中心 + 2 个最简 Tool — 新建 `app/services/agent/` 包
  - **三件套基础设施**：`tool_registry.py`（`Tool` / `ToolResult` frozen dataclass + 模块级 `_REGISTRY` + `register` / `get` / `list_all` / `list_openai_schemas` / `unregister` / `clear_registry_for_test`）+ `sandbox.py`（`@sandboxed(input_model, timeout_seconds)` 装饰器：pydantic 入参校验 + asyncio.wait_for 超时 + 异常归一 + elapsed_ms 注入 + deps 透传）+ `tools/` 子包（side effect import 自动注册）
  - **Tool 协议走 OpenAI tools schema**（`type: function` + `name` + `description` + `parameters` JSON schema）— DeepSeek-V3 / Qwen / 智谱 GLM-4 全兼容；BE-S2-007 LangGraph 主循环不需要再做协议适配
  - **入参 schema = pydantic BaseModel + `model_json_schema()`**：一处定义 = 入参文档 + 入参校验 + LLM schema 三合一；`Tool.to_openai_schema()` 主动 `pop("title", None)` 防御自托管 LLM (Qwen-2.5) 解析挑剔
  - **side effect 注册（不是显式 register list）**：BE-S2-006b / BE-S2-007 后会有 5+ Tool，加 Tool = 加文件 + 加 import 一行；测试隔离用 `clear_registry_for_test()` + `importlib.reload(tools_pkg.basic_info / .financial)` 重置
  - **沙盒走装饰器（不是注册中心硬包）**：让每个 Tool 文件自包含；任何 exception 在沙盒兜底为 `ToolResult.failure`，仅露 `Exception.__class__.__name__` 给 LLM（堆栈进 `logger.exception`，避免 LLM 学习反向越权）
  - **2 个最简 Tool（对接现有 IPO 表 / 不接 AKShare）**：`get_ipo_basic_info`（基本面 — 委托 `ipo_service.get_ipo`, Decimal/date 序列化为 JSON 友好类型）+ `get_financial_statements`（财务摘要 — 委托 `ipo_service.get_ipo_detail`, 缺数据走 `warning` 而非 failure，让 LLM 决定是改 Tool 还是回答"暂无数据"）
  - **OpenAI tool name 约束**（`^[a-zA-Z0-9_-]{1,64}$`）注册时强校验：防止后续 Tool 起名带空格 / 中文导致 LLM provider 拒收
  - **不在本 PR 做**：① LangGraph 主循环 / ReAct 步进（BE-S2-007）② Tool 调用回写 `chat_tool_calls`（BE-S2-007 在主循环内做，这里只管"算结果"）③ 限频 / 单用户日预算 / 单 session step cap（BE-S2-007 + BE-S2-008 配额）④ `hybrid_search` Tool 包装（BE-S2-006b 与 peers / sentiment / historical 一并落地）

缓存层（`app/cache/`）:

- `@cached(ttl_seconds=N, namespace="...")` — JSON 序列化的函数级缓存
- `@rate_limit(times=N, per_seconds=N, key_func=...)` — Lua 原子 INCR+EXPIRE 限流
- `invalidate_namespace(*namespaces)` — 按 `@cached` namespace 批量清缓存（Sprint 1.5；真 Redis 用 SCAN+UNLINK / InMemory 用 dict 遍历；fail-soft 保证 ingest 失败不被拖垮）
- `RealRedisClient` 走真 Redis；`InMemoryRedisClient` 走 dict+asyncio.Lock（单测/降级用）
- 所有 key 自动加 `xgzh:` 前缀
- `RateLimitExceeded` 由 `main.py` 全局 handler 转 HTTP 429（带 `Retry-After` header）

```python
from app.cache import cached, invalidate_namespace, rate_limit, RateLimitExceeded

@cached(ttl_seconds=1800, namespace="ipo")
async def fetch_ipo_basic(code: str) -> dict: ...

@rate_limit(
    times=1, per_seconds=60, namespace="otp",
    key_func=lambda phone: f"phone:{phone}",
)
async def send_otp(phone: str) -> None: ...

# ingest / write 路径完成后清掉相关 namespace, 让 GET 立刻拉到新数据
async def run_ingest_a_job():
    ...
    await invalidate_namespace("ipos:list", "ipos:detail")
```

LLM facade（`app/adapters/llm_client.py`，BE-S2-002）:

- **三入口** dispatch 三家 provider（硅基流动 / DeepSeek 官方 / 智谱），上层不感知厂商差异：
  - `chat()` 非流：返回 `ChatResult{ content, tool_calls, finish_reason, usage }`，给 LangGraph 决策步用（BE-S2-007）
  - `stream_chat()` 流（Sprint 1 兼容契约）：yield `str` token + 末尾自动追 `DISCLAIMER`
  - `astream_chat_with_meta()` 新流：yield `ChatStreamChunk{ delta? | (finish_reason, usage, tool_calls) }`，BE-S2-007 主循环用
  - `embed(texts, batch_size=32)`：批量嵌入，自动按 32 分批，输出维度对齐 settings.llm_embedding_dim（默认 1024 = `vector(1024)` 列）；输入数 ≠ 输出数直接抛 `LLMProviderError` 防 RAG 入库错位
  - `rerank(query, docs, top_n)`：直接 httpx 打硅基流动 `/v1/rerank` cohere 兼容协议，不走 LiteLLM；返回按 score 降序的 `(orig_idx, score)`
- **Provider 路由**：按 `model` 字符串前缀 dispatch — `openai/...` → 硅基流动 OpenAI 兼容 / `deepseek/...` → 官方 / `zhipu/...` → 智谱；未匹配抛 `LLMConfigError`
- **成本估算**：`_PRICE_CNY_PER_M_TOKENS` 内置 8 条价格条目（DeepSeek-V3 / V2.5 / glm-4-flash / bge-m3 / bge-reranker 等）；返回 `Decimal`（6 位小数），未匹配 fallback `0` + warn，防 BE-S2-007 写 `chat_token_usage.cost_cny` NOT NULL 触发
- **异常分层**：`LLMError(基类)` → `LLMConfigError`（密钥/路由）/ `LLMProviderError`（上游 5xx/网络/parse 失败），main.py 全局 handler 可统一映射 503/502
- **合规护栏**（Sprint 1 沿用）：`forbidden_pattern_filter()` / `ensure_disclaimer()` / `DISCLAIMER` 仍 export，老调用方零修改

```python
from app.adapters.llm_client import chat, embed, rerank, ChatResult

# Tool Use (BE-S2-007 LangGraph 决策步)
result: ChatResult = await chat(
    [{"role":"user","content":"分析 0700"}],
    tools=[{"type":"function","function":{"name":"get_basic_info"}}],
    temperature=0.0,
)
if result.tool_calls:
    for tc in result.tool_calls:
        # tc = {"id":"call_abc","type":"function","function":{"name":...,"arguments":'{...}'}}
        ...
# 落 chat_token_usage 直接读 result.usage.{prompt,completion,total}_tokens / cost_cny

# 招股书入库 (BE-S2-004)
emb = await embed([chunk1, chunk2, ...])  # 自动分批
assert emb.dim == 1024  # 对齐 vector(1024) 列

# 混合检索重排 (BE-S2-005)
rr = await rerank("腾讯估值", candidates, top_n=5)
top_indices = [orig_idx for orig_idx, _score in rr.results]
```

鉴权层 - OTP 发送（`app/adapters/sms/` + `app/services/otp_service.py`，BE-001）:

- `MockSMSAdapter` — dev 用，把 `phone+code` 打到 loguru，便于本地手测
- `AliyunSMSAdapter` — Sprint 2 接入占位
- `get_sms_adapter()` 按 `SMS_ADAPTER` 配置返回单例；`set_sms_adapter()` 单测注入
- `utils/phone.py` — E.164 归一化 + 5 国家码白名单（+86/+852/+853/+65/+886）+ `mask_phone` 脱敏
- OTP key: `xgzh:otp:{phone}`（明文 6 位数字，TTL=`OTP_TTL_SECONDS`，默认 300s）
- 限流 key: `xgzh:rate:otp_send:phone:{phone}`（TTL=`OTP_RESEND_INTERVAL_SECONDS`，默认 60s）
- 限流 key 用 **归一化后** 的 phone，因此 `13800138000` 与 `+8613800138000` 共享同一桶
- SMS 通道失败时（`SMSDeliveryError` → 502），自动 `consume_otp(phone)` 清掉刚存的 OTP

```bash
# 200
curl -X POST localhost:8000/api/v1/auth/otp/send \
  -H 'content-type: application/json' -d '{"phone":"13800138000"}'
# {"sent":true,"expires_in":300,"request_id":"...","masked_phone":"+86138****8000"}

# 在 server 日志中能看到:
# {"level":"INFO","logger":"app.adapters.sms.mock",
#  "msg":"[MOCK SMS] to=+8613800138000 code=126894 ttl=300s rid=..."}
```

鉴权层 - OTP 校验 + JWT 颁发（`app/security/jwt.py` + `app/services/auth_service.py`，BE-002）:

- HS256 access (30min) + refresh (30d) 双 token；带 `iss/aud/sub/typ/jti/iat/exp`
- `decode_token(token, expected_type=ACCESS_TOKEN_TYPE)` 强制按 typ 解，access ≠ refresh 不可互用
- OTP 校验用 `hmac.compare_digest` 常量时间比较
- 校验通过后 OTP 一次性消费（`consume_otp`）；错码不消费 → 用户在 5/5min verify 限流内可重试
- 用户不存在自动注册：生成 8 字符大写+数字 invite_code，冲突重试 5 次；phone 唯一约束撞了说明并发，降级为 fetch
- verify 限流：5 次/5min（`namespace="otp_verify"`），与 send 60s 桶物理隔离

```bash
# 1) send 拿 OTP（dev mock）
curl -X POST localhost:8000/api/v1/auth/otp/send \
  -H 'content-type: application/json' -d '{"phone":"13800138000"}'
CODE=$(redis-cli get 'xgzh:otp:+8613800138000')

# 2) login -> 200 + tokens
curl -X POST localhost:8000/api/v1/auth/login/phone \
  -H 'content-type: application/json' -d "{\"phone\":\"13800138000\",\"code\":\"$CODE\"}"
# {"user":{"user_id":"...","invite_code":"I3FB4CHU",...},
#  "tokens":{"access_token":"eyJ...","refresh_token":"eyJ...",
#            "token_type":"Bearer","expires_in":1800,"refresh_expires_in":2592000},
#  "is_new_user":true}

# 3) 同 OTP 复用 -> 401 otp_expired (一次性)
# 4) 错码 5 次 + 1 -> 第 6 次 429 too_many_requests (verify 限流)
```

⚠️ 生产前必做: `JWT_SECRET` 替换成 `openssl rand -hex 32` 生成的随机串, 否则启动时会打 ERROR 日志。

鉴权层 - Refresh + 黑名单（`app/security/blacklist.py` + `app/services/auth_service.py`，BE-004）:

- `POST /auth/refresh {refresh_token}` → **rotation**：拉黑旧 refresh 的 jti（TTL=旧 refresh 剩余有效期）+ 颁发新 access+refresh
- `POST /auth/logout` 需 `Authorization: Bearer <access>`，body 可选 `{refresh_token}`
  - 拉黑当前 access（即便 30min 还没过期，下一次请求立刻 401 `token_revoked`）
  - body 带 refresh 时一并拉黑；**sub 与 `current_user` 不一致直接拒绝**（防止恶意 logout 别人）
- 黑名单 key：`xgzh:blacklist:jti:{jti}`，value=`"1"`，每条只占 ~80 字节
- `is_jti_blacklisted` **fail-open**（Redis 故障返回 False，业务可用优先）；`blacklist_jti` 失败抛错（"以为登出了但其实没"是更严重的安全错觉）
- 黑名单粒度=**jti** 而不是 user_id，登出"这台手机"不影响 PC 端登录；"踢全员"是 `user_token_epoch` 机制，留待 BE-011 之后

```bash
# refresh -> 200 + 新 access/refresh, 旧 refresh 一次性
curl -X POST localhost:8000/api/v1/auth/refresh \
  -H 'content-type: application/json' \
  -d "{\"refresh_token\":\"<refresh>\"}"

# 旧 refresh 复用 -> 401 token_revoked
# 第 6 次/分钟 refresh -> 429

# logout (拉黑 access + refresh)
curl -X POST localhost:8000/api/v1/auth/logout \
  -H "Authorization: Bearer <access>" \
  -H 'content-type: application/json' \
  -d "{\"refresh_token\":\"<refresh>\"}"
# {"logged_out":true,"revoked_access":true,"revoked_refresh":true}

# 之后 /me 立刻 401 token_revoked
```

鉴权层 - 微信小程序登录（`app/adapters/wechat/` + `app/services/auth_service.py`，BE-005）:

- `POST /auth/login/wechat-mp {code}` → 调微信 `jscode2session` 拿 `(openid, unionid)` → 找/建用户 → 颁发 access/refresh
- 用户匹配优先级：**unionid > openid**；命中后 openid 字段总是覆盖为本次登录的最新值（跨小程序场景），unionid 字段用于回填（先按 openid 注册的老用户后续登录拿到 unionid 时补上）
- 错误模型分两类:
  - `WechatAuthError`（用户态: 40029 invalid_code / 41008 missing_code）→ 401 `wechat_code_invalid`，前端应让用户重新触发 `wx.login()`
  - `WechatAPIError`（系统态: -1 system busy / 45011 frequency / 40013 invalid appid / 网络超时 / HTTP 5xx / 非 JSON）→ 502 `wechat_upstream_error`，前端 retry
  - 未配置 AppSecret → 503 `wechat_mp_not_configured`（让运维补 .env）
  - 老用户 `status != 1` → 401 `user_disabled`，封禁状态不能借微信登录绕过
- **合规护栏**：`session_key` 永不进 dataclass、不入库、不打日志（违反即封号）
- 限流：同 `code[:32]` 1min 内 5 次，第 6 次 429（防 code 被盗后暴力试）
- 测试用 `set_wechat_mp_client(stub)` 直接注 stub class，不必 respx + ASGITransport 互相打架

```bash
# 200 (新用户首次登录, 含 unionid 的小程序)
curl -X POST localhost:8000/api/v1/auth/login/wechat-mp \
  -H 'content-type: application/json' \
  -d '{"code":"<wx.login() 拿到的 code>"}'
# {"user":{...,"invite_code":"H6EZQ70K"},
#  "tokens":{"access_token":"...","refresh_token":"...","expires_in":1800,"refresh_expires_in":2592000},
#  "is_new_user":true}

# 401 (40029 invalid code)
# {"detail":{"code":"wechat_code_invalid","message":"...","errcode":40029}}

# 502 (-1 system busy / 网络/HTTP 错)
# {"detail":{"code":"wechat_upstream_error","message":"...","errcode":-1}}

# 503 (未配置)
# {"detail":{"code":"wechat_mp_not_configured","message":"微信小程序登录暂未启用..."}}
```

⚠️ 生产前必做: 在 `.env` 配 `WECHAT_MP_APP_ID` / `WECHAT_MP_APP_SECRET`（微信公众平台 → 小程序 → 开发设置）。

IPO 详情字段聚合（`app/services/ipo_service.py::get_ipo_detail`，BE-009）:

- 新 schema `IPODetail`（继承 `IPOItem` 加 6 个字段）
  - `prospectus_url`：招股书 PDF
  - `sponsors` / `underwriters`：保荐人 / 承销商列表
  - `highlights` / `risks`：亮点 / 风险点（BE-018 招股书 RAG 落地后填，当前从 `extra` JSONB 读）
  - `financial_summary`：财务摘要 dict（同上）
- A/US 走 DB（`SELECT * FROM ipos WHERE code=? AND market=?`），HK 走 `fetch_hk_ipos` seed 扫描
- `_orm_to_detail(row)`：把 `ipos.extra` JSONB 中已结构化的 `highlights` / `risks` / `financial_summary` 提到顶层；其余 `extra.*` 字段（包括 `internal_*`、`one_lot_winning_rate` 等内部 metadata）**不漏给客户端**
- 类型不对（如 `extra.highlights` 是 str 不是 list）时优雅降级为空 list，**绝不 5xx**
- `@cached(ttl=1800s, namespace="ipos:detail")`，`skip_if_none=True` 防 404 穿透；不存在的 code 不进缓存，运营/cron 后续 ingest 入库后立即可见
- 列表 `ipos:list` 10min vs 详情 `ipos:detail` 30min：详情字段（招股书/保荐人）变化慢，给得更长
- 404 错误码标准化：`{"detail": {"code": "ipo_not_found", "message": "IPO ... not found"}}`，与登录/邀请码错误体同构
- 仍保留 `get_ipo(code) -> IPOItem | None` 给 `agent_service.diagnose_stream` 用：Agent prompt 只需基础信息，避免被 30min 详情缓存干扰

```bash
# 1) HK seed 命中: 占位字段都是 None / []
curl -s localhost:8000/api/v1/ipos/02015.HK | jq

# 2) A 股 (DB hit): extra 没填时 highlights=[] risks=[] financial_summary=null
curl -s 'localhost:8000/api/v1/ipos?market=A&size=1' | jq '.items[0].code'
curl -s localhost:8000/api/v1/ipos/<code> | jq

# 3) 运营手填 sponsors / extra.highlights:
psql -U xgzh -d xgzh -c "UPDATE ipos SET sponsors='[\"中金\",\"华泰\"]'::jsonb,
  extra=jsonb_set(coalesce(extra,'{}'::jsonb),'{highlights}','[\"亮点A\"]'::jsonb)
  WHERE code='<code>';"

# 4) 清详情缓存 (30min TTL 太长, 手测时 bust):
redis-cli --scan --pattern 'xgzh:cache:ipos:detail:*' | xargs -I{} redis-cli DEL {}

# 5) 再请求 -> 看到新字段
curl -s localhost:8000/api/v1/ipos/<code> | jq '.sponsors, .highlights'

# 6) 不存在 -> 404
curl -sw '\nHTTP=%{http_code}\n' localhost:8000/api/v1/ipos/000999.SZ
# {"detail":{"code":"ipo_not_found","message":"IPO 000999.SZ not found"}}
```

用户自选股（`app/services/favorite_service.py`，BE-010）:

- **市场后缀白名单**：`_parse_code` 把 `0700.HK` / `600519.SH` / `BABA.US` 反推 market；`.HK` → HK，`.SH` / `.SZ` / `.BJ` → A，`.US` → US；其它一律 400 `favorite_code_invalid`，避免脏数据进表
- **PG `INSERT ... ON CONFLICT DO UPDATE` 单 SQL 幂等**：`(user_id, ipo_code, market)` 复合主键自动去重；`DO UPDATE SET notify_on_subscribe=...` 让用户重新收藏时可切换"打新提醒"开关
- **`RETURNING (xmax = 0)` 区分 INSERT vs UPDATE**：PG 老 trick，`xmax=0` 表示该行刚被本事务 INSERT，非 0 表示走了 `ON CONFLICT DO UPDATE`；比"再发一条 SELECT 判已存在"省一次 round-trip
- **删除幂等**：`DELETE` 后看 `rowcount`，0 行也返 200 + `removed=False`，前端不需要 try/catch
- **LEFT JOIN ipos 而非 INNER JOIN**：HK seed code 当前不在 `ipos` 表，LEFT JOIN 让自选页仍能渲染"占位卡片"，行情字段为 `null`
- **`one_lot_winning_rate` 从 `extra` JSONB 提到顶层**：与 BE-009 同策略，`FavoriteItem` 不暴露 `extra`，schema 演进可控
- **3 个路由全部 `Depends(get_current_user)`**：401 复用 BE-003 的 6 种 reason
- **MVP 不分页**：单用户自选 < 100 支假设；schema 已含 `total` 字段为后续分页留位

```bash
# 1) 拿 access_token (省略 OTP 步骤)

# 2) 添加自选 (DB-backed A 股)
curl -X POST localhost:8000/api/v1/favorites \
  -H "Authorization: Bearer <access>" -H 'content-type: application/json' \
  -d '{"code":"600519.SH"}'
# {"ok":true,"code":"600519.SH","market":"A","created":true,
#  "notify_on_subscribe":true,"favorited_at":"..."}

# 3) 重复添加 + 切 notify off → created=False (幂等)
curl -X POST localhost:8000/api/v1/favorites \
  -H "Authorization: Bearer <access>" -H 'content-type: application/json' \
  -d '{"code":"600519.SH","notify_on_subscribe":false}'
# {"ok":true,"created":false,"notify_on_subscribe":false,...}

# 4) HK seed code 也可收 (ipos 表无, list 时 LEFT JOIN 字段为 null)
curl -X POST localhost:8000/api/v1/favorites \
  -H "Authorization: Bearer <access>" -d '{"code":"02015.HK"}'

# 5) GET 列表 (混合 A/HK, 按 favorited_at DESC)
curl -H "Authorization: Bearer <access>" localhost:8000/api/v1/favorites
# {"items":[
#    {"code":"02015.HK","market":"HK","name":null,"listing_date":null,"status":"unknown",...},
#    {"code":"600519.SH","market":"A","name":"贵州茅台","listing_date":"2025-01-15","status":"listed",...}
#  ], "total":2}

# 6) DELETE 幂等
curl -X DELETE -H "Authorization: Bearer <access>" \
  localhost:8000/api/v1/favorites/600519.SH
# {"ok":true,"removed":true}
curl -X DELETE -H "Authorization: Bearer <access>" \
  localhost:8000/api/v1/favorites/600519.SH
# {"ok":true,"removed":false}

# 7) 无后缀 → 400
curl -X POST localhost:8000/api/v1/favorites \
  -H "Authorization: Bearer <access>" -d '{"code":"BABA"}'
# {"detail":{"code":"favorite_code_invalid","message":"code 必须带市场后缀..."}}
```

推送 token 注册（`app/services/push_service.py`，BE-011）:

- **平台白名单 `ios|android|wxmp|h5`**：Pydantic Literal 校验，非法值直接 422
- **`device_id` 强制必填非空**：PG `UNIQUE (user_id, platform, device_id)` 在 `device_id IS NULL` 时**不去重**（NULL 互不相等的 SQL 老坑）；强制非空让 `ON CONFLICT` 行为可预期，前端只需传一个稳定的设备标识（小程序用 openid hash、H5 用 cookie hash）
- **响应不 echo `token`**：APNs / FCM token 是敏感凭据，泄露后第三方可代发垃圾消息；客户端本身就持有，无需后端回传
- **`POST` 单 SQL 幂等**：`INSERT ... ON CONFLICT (user_id, platform, device_id) DO UPDATE SET token = EXCLUDED.token, is_active = true`，加 `RETURNING (xmax = 0)` 一次拿到 `created` 标志（沿用 BE-010 思路）；同 device 复发 = 仅刷新 token + 重新激活，不新增行
- **`DELETE` 复合条件 `(user_id, platform, device_id)`**：杜绝越权删别人的 token；不存在也返 200 + `removed=false` 保持幂等
- **覆盖时强制 `is_active = true`**：将来加"运营禁用 token"功能后，用户重新注册同 device 会自动重新激活，避免"明明 APP 在前台却收不到推送"
- Sprint 4 推送实施：`push_service.list_user_tokens(user_id)` 取活跃 token 群发；本 Sprint 只养名单不发推

```bash
# 1) 注册 (created=true, 响应没 token 字段)
TOK=$(printf "a%.0s" {1..64})
curl -X POST localhost:8000/api/v1/push/tokens \
  -H "Authorization: Bearer <access>" -H 'content-type: application/json' \
  -d "{\"platform\":\"ios\",\"token\":\"$TOK\",\"device_id\":\"iphone-15\"}"
# {"ok":true,"id":1,"platform":"ios","device_id":"iphone-15","is_active":true,"created":true,"registered_at":"..."}

# 2) 同 device 复发新 token (created=false, id 不变, DB 里 token 被覆盖)
curl -X POST localhost:8000/api/v1/push/tokens \
  -H "Authorization: Bearer <access>" -H 'content-type: application/json' \
  -d '{"platform":"ios","token":"b...b","device_id":"iphone-15"}'
# {"ok":true,"id":1,"created":false,...}

# 3) 注销 (removed=true)
curl -X DELETE \
  -H "Authorization: Bearer <access>" \
  'localhost:8000/api/v1/push/tokens?platform=ios&device_id=iphone-15'
# {"ok":true,"platform":"ios","device_id":"iphone-15","removed":true}

# 4) 重复注销 (removed=false, 仍 200, 幂等)
curl -X DELETE \
  -H "Authorization: Bearer <access>" \
  'localhost:8000/api/v1/push/tokens?platform=ios&device_id=iphone-15'
# {"ok":true,"removed":false}

# 5) platform 非白名单 → 422
curl -X POST localhost:8000/api/v1/push/tokens \
  -H "Authorization: Bearer <access>" -H 'content-type: application/json' \
  -d '{"platform":"symbian","token":"...","device_id":"x"}'
```

邀请码（`app/services/invite_service.py`，BE-006）:

- 注册时 BE-002/BE-005 在同一事务里把 `users.invite_code`（8 字符大写+数字）镜像到 `invite_codes` 表（owner_user_id=新用户、`max_usage=NULL` 无限、`is_active=true`、`note='personal'`）
- `POST /invite/bind {code}` 在登录态下绑定 referrer：
  - **一次性**：`users.invited_by` 一旦写入不可改。两层防御：service 层 `invited_by IS NOT NULL` fast-fail + 路由 conditional UPDATE `WHERE invited_by IS NULL` 防并发
  - **自禁**：`code == own_invite_code`（含大小写归一）→ 400 `invite_self_binding`
  - **`SELECT ... FOR UPDATE`** 锁住 `invite_codes` 行后做 `usage_count += 1` + max_usage/expires_at 校验，防超额
  - **运营码 `owner_user_id IS NULL`** MVP 不接受作为 referrer（留给"渠道追踪"功能）
  - schema 层 `strip + upper` 自动归一，DB 端只存大写
- 7 类错误码（`detail.code` 区分）：`invite_code_not_found` (404) / `invite_already_bound` / `invite_self_binding` / `invite_code_inactive` / `invite_code_expired` / `invite_code_exhausted` / `invite_code_not_personal`（皆 400）
- 限流 10/min/user（`namespace=invite_bind`）防暴力扫码

```bash
# 200 happy
curl -X POST localhost:8000/api/v1/invite/bind \
  -H "Authorization: Bearer <invitee access>" \
  -H 'content-type: application/json' \
  -d '{"code":"ABCD1234"}'
# {"ok":true,"referrer_user_id":"...","referrer_invite_code":"ABCD1234","bound_at_usage_count":1}

# 400 already_bound (二次绑)
# 400 self_binding (用自己的码)
# 404 not_found
# 401 token_missing (未登录)
```

鉴权层 - 当前用户依赖（`app/security/deps.py`，BE-003）:

- `get_current_user(request, session)` — 强校验依赖，业务路由 `Depends(get_current_user)` 即可
- `get_optional_user(request, session)` — 匿名友好，未登录返回 `None`，给 IPO 列表/Agent 试用这种公开+个性化混合接口用
- 不复用 FastAPI 的 `HTTPBearer`：`auto_error=False` 把"无 header"和"非 Bearer scheme"折叠成同一个 `None`，丢失 401 reason；这里手动解析 `Authorization` header
- 6 种 401 reason（`detail.code` 区分）+ 全部带 `WWW-Authenticate: Bearer realm="xgzh"`：
  - `token_missing`：缺 Authorization header
  - `token_scheme_invalid`：scheme 不是 Bearer（如 Basic）
  - `token_invalid`：签名错 / 篡改 / aud / iss 错 / **typ 不是 access**（refresh 不能当 access 用）
  - `token_expired`：access 已过期 → 前端 silent refresh
  - `token_revoked`：jti 在 BE-004 黑名单（已 logout 或被风控踢下线）
  - `user_not_found`：sub UUID 在 DB 不存在或已软删
  - `user_disabled`：`status != 1`
- token 内 `status` 不可信：每次都查 DB 一次，被禁用/软删的用户即使握合法 token 也立刻 401

```bash
# 拿到 access_token 后:
curl -i -H "Authorization: Bearer <access>" localhost:8000/api/v1/me
# 200 {"user_id":"...","invite_code":"...","region":"CN","status":1,...}

# refresh 当 access 用 -> 401 token typ mismatch
curl -i -H "Authorization: Bearer <refresh>" localhost:8000/api/v1/me
```

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

**Sprint 1.5 已封装到 `Makefile`，3 条命令搞定**：

```bash
# 起基础设施 (PG + Redis); 已起则跳过
cd ../../infra && docker compose up -d postgres redis
cd ../apps/api

# 1. 测试库初始化 (幂等; 含 pgcrypto extension)
make test-db-init

# 2. 跑全部测试 (单元 + 集成; 等价 CI)
make test-all
# → 373 passed in ~36s (Sprint 1 + BE-S2-001/002/003/000/004/005/006a)

# 或者只跑 e2e (3 条 ~3s)
make test-e2e

# 或者只跑单元 (无 DB 依赖, 集成自动 skip, ~5s)
make test-unit
```

`make help` 列全部 7 个 target（`help` / `test-db-init` / `test-unit` / `test-e2e` / `test-all` / `lint` / `typecheck`）。

底层等价命令（不想用 Makefile 时）:

```bash
# 单测（无 DB 集成测试自动跳过）
uv run pytest

# 含 DB 集成测试（迁移 up/down/idempotent + e2e 主路径）
# 先建测试库 xgzh_test:
psql -U postgres -c "CREATE DATABASE xgzh_test OWNER xgzh;"
psql -U postgres -d xgzh_test -c "CREATE EXTENSION pgcrypto;"
# pgvector extension 留给 BE-S2-003 PR 加 (Sprint 2)

XGZH_TEST_DATABASE_URL='postgresql+asyncpg://xgzh:xgzh_dev_pass@localhost:5432/xgzh_test' \
  uv run pytest -q
```

### QA-001 · e2e 集成测试

`tests/integration/` 是 QA-001 落地的"金线"e2e: 一条用例打通 注册 → token → /me → /ipos → /agent/diagnose SSE → 收藏 6 个模块。CI 友好特性：

- **不依赖真 LLM Key**: `fake_llm` fixture monkey-patch `llm_client.stream_chat`，返回 5 段固定 token + 真 `DISCLAIMER` 字符串；本地 `.env` 即使配了 SILICONFLOW_API_KEY 也不会被偷打。
- **不依赖真短信网关**: `mock_sms` fixture 注入 `MockSMSAdapter`；测试用例直接 `otp_service.store_otp()` 埋码，跳过短信投递。
- **不依赖真 Redis**: `redis_client` fixture 用 `InMemoryRedisClient`，覆盖 INCR/EXPIRE/Lua 路径，与 `RealRedisClient` 行为一致（BE-005）。
- **没设 `XGZH_TEST_DATABASE_URL` 时整个 session skip**: 顶层 `tests/conftest.py` 的 `db` marker hook 兜底，CI 不会因没起 PG 红。

预期 `3 passed`，详见 `spec/08-sprint-1-backlog.md` §QA-001 / §Sprint 1.5。

## 项目结构

```
app/
├── api/v1/         # 路由 (ipos / agent / auth / me / invite / favorites / push)
├── core/           # 配置、日志
├── services/       # 业务逻辑 (ipo / agent / otp / user / auth / invite / favorite / push)
├── adapters/       # 外部数据源 / 通道
│   ├── akshare_client.py
│   ├── llm_client.py
│   ├── sms/        # SMS 通道 (base / mock / aliyun / factory)
│   └── wechat/     # 微信小程序 jscode2session (BE-005)
├── security/       # JWT 颁发 / 解析 + FastAPI 鉴权依赖 + 黑名单
│   ├── jwt.py        # HS256 access + refresh, 严格 typ 隔离
│   ├── deps.py       # get_current_user / get_optional_user
│   └── blacklist.py  # jti 粒度黑名单 (Redis SETEX, fail-open 读)
├── schemas/        # Pydantic 模型 (ipo / agent / auth / favorite / push)
├── utils/          # 通用工具 (phone E.164 + mask)
├── db/             # SQLAlchemy 2.0 async Base + ORM models
│   ├── base.py
│   └── models/     # users, auth, invite, ipo, push 等
├── cache/          # Redis 客户端封装 + @cached / @rate_limit 装饰器
└── main.py
alembic/
├── env.py
└── versions/       # 0001_init_core_schema.py …
```

详见 `.cursor/rules/10-backend-fastapi.mdc` 与 `.cursor/rules/40-database.mdc`。
