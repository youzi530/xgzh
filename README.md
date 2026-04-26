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
  - ✅ **BE-009**：`GET /ipos/{code}` 字段聚合 / 多源 merge（新 schema `IPODetail` 加 `sponsors` / `underwriters` / `prospectus_url` / `highlights` / `risks` / `financial_summary`；`extra` JSONB 提取顶层字段；`@cached(ttl=1800s, namespace="ipos:detail")` `skip_if_none=True` 防 404 穿透；404 标准化错误码 `ipo_not_found`）
  - ✅ **BE-010**：用户自选股 + API（`POST/DELETE /favorites` PG `INSERT ... ON CONFLICT DO UPDATE` 单 SQL 幂等，`RETURNING (xmax=0)` 区分 INSERT/UPDATE 路径；`GET /favorites` LEFT JOIN ipos 拿最新行情；前端只持 `code` 后缀反推 market；HK seed code 也可收藏；3 个路由全部 `Depends(get_current_user)` 401 闸守）
  - ✅ **BE-011**：推送 token 注册（`POST /push/tokens` PG `ON CONFLICT (user_id, platform, device_id) DO UPDATE` 幂等覆盖；`DELETE /push/tokens?platform=&device_id=` 单 SQL 幂等；响应**不回显 token**，敏感凭据保护；`device_id` 强制必填非空规避 PG NULL UNIQUE 老坑；Sprint 4 推送实施时调 `list_user_tokens` 群发）
  - ✅ **FE-001**：登录页（手机 OTP + 微信一键，UniApp Vue3）
    - 双 Tab：手机号 + 验证码（H5 / 小程序 / App 全平台）/ 微信一键（仅 `MP-WEIXIN` 条件编译）
    - 60s 倒计时（前端镜像 + 429 后端兜底拉起，防前端时钟漂移）
    - 协议勾选 + 合规 footer（spec/06 §法律隔离要求）
    - `apps/mp/api/auth.ts` 字段名 1:1 对齐 BE-001/002/005 的 OAuth2 习惯，避免双向翻译；`parseAuthError` 把后端 `detail.code` 拆给业务分支
    - `apps/mp/utils/auth-storage.ts` 拆 5 个 storage key（access/refresh/user/两个过期时间戳），含 60s 安全边际的 `isAccessTokenFresh` 给 FE-002 silent refresh 接力
    - 错误码差异化 UX：`otp_invalid` 清验证码、`otp_expired` 重置倒计时、`wechat_mp_disabled` 自动切手机号 Tab
    - 首页 hero 加"登录 / 注册"胶囊（已登录显示昵称首字头像，点击占位提示 FE-003）
  - ✅ **FE-004**：首页瀑布流 + 今日打新卡片 + 打新日历
    - `apps/mp/api/ipo.ts` 升级到 BE-008 完整签名（`page` / `size` / `status` / `industry`）+ 抽 `statusLabel` / `statusPalette` 给卡片色块复用
    - `apps/mp/components/IPOCard.vue` 双密度（default / hero）：右上角状态色块（`subscribing` 金 / `upcoming` 蓝 / `listed` 灰 / `withdrawn` 红）+ 智能副标题（申购截止 / 上市日 / 申购窗口）
    - `apps/mp/components/IPOCalendar.vue` 按申购开始日 / 上市日 group：顶部横滚日期 chip（含数量徽标）+ 分组卡片列表，"待定"沉底
    - `apps/mp/pages/index/index.vue` 重构：market tab + 视图切换（列表 / 日历）+ status chip 多筛选 + 列表头插入"今日打新"hero 卡（最多 3 只 subscribing）+ 触底分页 + 数据来源 footer aggregate
  - ✅ **FE-005**：新股详情页 — 关注按钮 + 招股要点
    - `apps/mp/api/ipo.ts` 的 `fetchIPODetail` 升级到 `IPODetail`（叠加 `prospectus_url` / `sponsors` / `underwriters` / `highlights` / `risks` / `financial_summary` 6 个 BE-009 深度字段）
    - `apps/mp/api/favorites.ts`（新建）+ `apps/mp/stores/favorites.ts`（Pinia store, 集中持自选 + `isFavored(code)` O(1) 查询 + 乐观更新失败回滚 + watch `auth.loggedIn` 自动 reset）
    - `apps/mp/components/FavoriteButton.vue`（未登录跳登录 modal / 已登录乐观切换 / 错误码分类 toast / `default | compact` 双密度）
    - `apps/mp/pages/ipo/detail.vue` 重构：顶部红色风险 banner + Header（status badge + 关注按钮）+ 6 格基本信息卡 + 4 tab（基本面 / 保荐承销 / 亮点 / 风险，财务摘要 dict 容错渲染）+ AI 诊断 CTA（"VIP 限免"角标占位）+ 数据来源行
    - 跨 store 联动用 watch 而非反向 import，箭头单向 favorites → auth，避免循环依赖
  - ✅ **FE-006**：自选列表 Tab
    - `apps/mp/pages/me/favorites.vue`（新建）：顶部 stats 条（已关注 N / 申购中 X 金色高亮）+ `IPOCard` 列表（适配器 `toIPOItem` 把 `FavoriteItem` 缺失字段填 null）+ 长按 ActionSheet → modal 二次确认 → store 移除 + 空态（图标 + 文案 + "去发现新股"CTA）+ 下拉刷新
    - `apps/mp/pages.json`：注册 `/pages/me/favorites` + `enablePullDownRefresh: true`
    - `apps/mp/pages/me/index.vue`：插入"我的自选"入口卡片（VIP 卡下方），右侧显示自选数量徽标，进个人中心时预热 `favStore.loadOnce()`
    - 跨页响应式验证：详情页 ★ 关注 → 自选列表立即同步（store 单源真相）；详情页 ☆ 取消 → 自选列表立即少一项
  - ✅ **QA-001**：API 集成测试套件（Sprint 1 收尾）
    - `apps/api/tests/integration/conftest.py`（新建）：复合 fixtures — Alembic schema reset（module 范围）+ `truncate_all`（function 范围）+ `InMemoryRedisClient` + `MockSMSAdapter` + `fake_llm`（monkey-patch `llm_client.stream_chat` 返回固定 token + 真 `DISCLAIMER`）+ 一站式 `client`（`httpx.ASGITransport`）
    - `apps/api/tests/integration/test_e2e_ipo_diagnose.py`（新建）：3 条 e2e — 主路径 注册→token→/me→/ipos→/ipos/{code}→/agent/diagnose SSE→收藏闭环；退化路径 unknown code 兜底；护栏路径 `/agent/diagnose` 匿名调用允许（spec/04 §1.3）
    - 修一处隐藏 bug：`patch_session_factory` 之前漏 patch `ipo_service` module-level `get_session_factory` 引用，导致 e2e 走 `/ipos` 列表时拿不到 seed 数据。conftest 现在三处都 patch（`db_pkg` / `ipo_ingest_service` / `ipo_service`）
    - SSE 帧解析: `_parse_sse_frames(body)` 按 `\n\n` split + `event:` / `data:` 行解析为 `[(event_type, parsed_dict)]`，比照 `event: start` / `delta` * N / `end` 三类帧 + body 里"不构成投资建议"做合规验收
  - ✅ **FE-003**：个人中心 + 设置 + VIP 入口（无支付）
    - 资料卡（昵称首字头像 / 区域本地化 / 邀请码点击复制）+ VIP 占位卡 + 邀请绑定卡 + 设置区 + 退出登录
    - 邀请绑定接 BE-006，前端做长度校验 + 自禁 + 大写归一，7 类错误码（`invite_code_not_found` / `invite_self_binding` / `invite_already_bound` / `invite_code_inactive` / `invite_code_expired` / `invite_code_exhausted` / `invite_code_not_personal`）逐个映射文案
    - 已绑状态用 `xgzh.invite.bound_referrer` storage 兜底（后端 `UserPublic` 暂不暴露 referrer 字段）；缓存丢失时 `invite_already_bound` 自动翻译为灰态显示
    - 退出登录走 `auth.logout()`（FE-002 store action 内部含拉黑后端 + clearSession）+ `uni.reLaunch('/pages/index/index')`，并清 referrer 缓存防串号
    - 顶部固定"工具属性"合规角标（spec/06 §法律隔离），设置项暂用 modal 占位等三份正式文本到位后切 webview
  - ✅ **FE-002**：Auth Pinia store + uni.request 拦截器
    - `apps/mp/stores/auth.ts`：响应式 store，hydrate from storage；`setSession` / `setTokens` / `clearSession` / `refresh` / `logout` 5 个 action；`accessToken` / `refreshToken` / `user` / `loggedIn` / `isAccessFresh` / `isRefreshFresh` 响应式 getter
    - **silent refresh 并发去重**：单 inflight Promise，多个请求同时 401 仅触发一次 refresh，避免 BE-004 rotation 拉黑刚发的 refresh_token 把用户踢下线
    - `apps/mp/utils/request.ts`：自动注入 `Authorization: Bearer`；401 `token_expired` → silent refresh + 重试一次（`_isRetry` 标志防无限重试）；其它 401（`token_invalid` / `revoked` / `user_disabled`）→ `clearSession` + 跳登录；`skipAuth` 给鉴权接口豁免
    - 跳登录用 `navigateTo` 保留页面栈；`_redirectingToLogin` 防抖 + `getCurrentPages()` 豁免登录页本身，杜绝并发 401 / 死循环
    - `apps/mp/api/auth.ts` 补 `refreshToken` (BE-004) + `logout` (BE-004)；`sendOtp` / `loginPhone` / `loginWechatMp` / `refreshToken` 全部 `skipAuth: true`
    - 首页改用 `storeToRefs(authStore)` 响应式订阅，删 `onShow` 手动 refresh；登录页改用 `auth.setSession(resp)`
  - ✅ **Sprint 1.5 收尾包**：缓存失效 hook + Makefile DX 整理（详见 [`spec/08` §Sprint 1.5](./spec/08-sprint-1-backlog.md#-sprint-15-收尾包----跨-sprint-12-的破窗清理)）
    - `cache.invalidate_namespace("ipos:list", "ipos:detail")` 接入 `run_ingest_a_job` 末尾，SCAN + UNLINK 实现，关闭 BE-008 / BE-009 缓存 stale 遗留
    - `Makefile`：`help` / `test-db-init`（幂等 createdb + pgcrypto）/ `test-unit` / `test-e2e` / `test-all` / `lint` / `typecheck`，关闭 QA-001 测试库初始化遗留
    - 7 条新 cache 单测（前缀边界 / fail-soft / 不误删限流 key 等不变量锁定）
- **后端测试**：
  - 无 DB：`cd apps/api && uv run pytest -q` ⇒ 120 passed / 136 skipped（含 BE-S2-002 facade 24 条单测）
  - 有 DB：`make test-all` ⇒ **500 passed in ~30s**（465 → 500，新增 35 条 BE-S2-008 测试：`tests/test_sliding_window.py` 12（InMemoryRedisClient 滑动窗口 3 个高层接口的不变量：record 同 member 不重复 / 不同 member 累加 / 出窗清旧 / oldest_ms 边界 / count 只读 / key 隔离 / 完整生命周期）+ `tests/test_agent_quota.py` 18（resolve_plan 匿名/FREE/VIP CSV 大小写空格 / QuotaStatus.has_quota & to_dict / check_quota VIP 跳 Redis & FREE 不消费 & 匿名 / record_usage 累加 & 超额抛 & per-user 隔离 & per-IP 隔离 & 默认 uuid member 不停滞 / retry_after 边界）+ `tests/integration/test_chat_diagnose_quota.py` 5（匿名 anon_per_window=1 → 第 2 次 429 含 retry-after header / 429 时 user_message 不落 chat_messages / FREE 用户 free_per_window=1 → 第 2 次 429 plan=free / 多用户 quota key 隔离 / VIP whitelist 命中连调 3 次都 200）；累计 11 张 DB 表（不动 schema）+ 0004_fts 全文搜索；以及更早的 54 条 BE-S2-007 测试：`tests/test_agent_citation.py` 14（build_citations 顺序+chunk_id 去重+空 chunk_id 跳过+score float cast+长 snippet 截断+短文本不带省略号 / validate 保留有效 [n] / 剔除越界 / 空文本 / 无 [n] 直通 / 全部越界 / assemble happy / 无结果 / Citation.to_dict 字段集）+ `tests/test_agent_system_prompt.py` 5（红线全在 / 注册 tool 全在 / IPO 锚点段落 / 无 IPO 锚点段落不在 / [n] 引用约定）+ `tests/test_agent_graph.py` 18（_result_preview 4 形 / _serialize_tool_result_for_llm 3 形 / _aggregate_usage 2 形 / _resolve_provider 4 形 / 5 AgentEvent dataclass frozen 形）+ `tests/integration/test_agent_persistence.py` 12（session 新建 / resume / title 截断 / bogus session_id 兜底 / user+assistant 落表 / tool role openai_tool_call_id 留存 / 时序 / session_history 跳 tool role / pending→ok 流转 / 4KB error 截断 / Decimal cost 透传 / 级联删除）+ `tests/integration/test_chat_diagnose.py` 5（无 tool 直接终答 / get_ipo_basic_info tool call → 终答 / LLMProviderError 友好 SSE / 续聊 history 注入 / hybrid_search → 引用源装配）；累计 11 张表 + 0004 tsvector 全文索引 + 混合检索 vector+BM25+RRF+reranker + Tool 注册中心 + 沙盒 + 6 个 Tool 全数注册到位 + **ReAct Agent 主循环 + 引用源装配 + SSE 端层全链路**）

### 🚀 Sprint 2 进行中 — AI Agent + RAG（核心壁垒）

16 PR / ~14d 排期，详见 [`spec/09-sprint-2-backlog.md`](./spec/09-sprint-2-backlog.md)。已落地：

- ✅ **BE-S2-001**（4 张会话表）：`chat_sessions` / `chat_messages` / `chat_tool_calls` / `chat_token_usage` + Alembic 0002 + 6 个二级索引 + 6 条集成测试（迁移幂等、级联策略、append-only 守护齐验证）
- ✅ **BE-S2-002**（LLM facade）：单文件 `app/adapters/llm_client.py` 重构 + `chat / embed / rerank` 三入口 + 5 个 frozen dataclass（`TokenUsage / ChatResult / ChatStreamChunk / EmbeddingResult / RerankResult`）+ 3 层异常 + 8 条 hardcoded 成本表（CNY/M tokens）+ 24 条单测（路由 / 成本 / tool_calls 跨帧聚合 / 自动分批 / respx rerank 全覆盖）。Sprint 1 老 4 处调用方零修改
- ✅ **BE-S2-003**（`ipo_documents` 扩展 + 防重）：Alembic 0003 给已有 `ipo_documents` ALTER 6 列（`chunk_index` / `token_count` / `content_hash` / `embedding_model` / `embedding_dim` / `lang`）+ 2 索引（`(doc_id, content_hash)` partial UNIQUE 防重 + `(doc_id, chunk_index)` partial 排序）+ 8 条 PG 真跑集成测试（schema 形状 / partial UNIQUE / NULL 共存 / `<=>` cosine ANN 实查 / downgrade idempotent）。BE-S2-004 招股书入库直接 `ON CONFLICT (doc_id, content_hash) DO NOTHING` 防重灌；多版本向量共存留口
- ✅ **BE-S2-000**（HK IPO 真源接入）：`hkex_client` 抓 hkexnews `applicants_c.htm` 列表 + BeautifulSoup 解析（公司名 / 递交日 / 招股书 PDF URL）+ `AP{yymmdd}{slug:5}.HK` 16 字符占位 code 贴 `VARCHAR(16)`+ `httpx.AsyncClient` + `Semaphore(2)` 限并发 + 失败兜底返回空。`run_ingest_hk_job` 走 `upsert_ipos` 复用 BE-007 写入路径；**关键守护：`extra` 改 `jsonb || jsonb` 浅合并**（防 BE-S2-004 RAG 写入的 `highlights` / `risks` 被 ingest 整体覆盖）。`scheduler/__init__.py` 注册 `ipo_ingest_hk_initial` + `ipo_ingest_hk_cron`（默认 `9,17` HKT 二刀流）。`ipo_service` 切 DB 路径 + cold-start seed 兜底首次部署。新增 17 条测试
- ✅ **BE-S2-004**（招股书 PDF 入库流水线）：3 层架构 — `app/adapters/pdf_loader.py` httpx 流式下载（Content-Length 提前拒 + 累计字节兜底防对端不诚实）+ pypdf 6.x 抽页文（单页失败 logger.warning + skip，全空抛 `PDFFetchError`）；`app/services/rag/chunker.py` 段落→句子→字符 3 层 fallback 切分 + CJK/英文启发式 token 估算（不引 tiktoken / transformers）；`app/services/rag/prospectus_ingest_service.py` 编排 fetch→extract→chunk→embed→upsert，5 阶段 stats + 失败 stage 定位 + `ON CONFLICT (doc_id, content_hash) WHERE content_hash IS NOT NULL DO NOTHING` 幂等（**关键 bug 自查：partial UNIQUE 必须给 `index_where` 谓词**, 否则 `InvalidColumnReferenceError`）。`doc_id = sha256(url)[:32]`，URL 改版自动新版本共存；embed dim 校验防 vector(1024) 索引污染。本 PR 不挂 scheduler（招股书几十 MB × N 撑爆带宽），手动入口给 Sprint 3 运营触发面板留口。新增 33 条测试
- ✅ **BE-S2-005**（混合检索 + RRF + reranker）：Alembic 0004 给 `ipo_documents` 加 `tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', regexp_replace(text, E'([\u4e00-\u9fff])', E'\\1 ', 'g'))) STORED` + GIN 索引；新建 `app/services/rag/hybrid_search.py`。**不上 zhparser**（装难 + CI 跑不了 + 字典维护重）→ 用 PG `simple` config + 中文字符级预切替代，**单一真相**：写入端 0004 与查询端 `_cjk_presplit` 用同样正则 `[\u4e00-\u9fff]`。三阶段架构：vector 召回（HNSW cosine `embedding <=> CAST(:q AS vector)`）+ BM25 召回（`tsv @@ plainto_tsquery + ts_rank_cd`）+ Reciprocal Rank Fusion（Cormack 2009, k=60）+ bge-reranker-v2-m3 cross-encoder 二阶段精排（pool=20, final=5）。**过滤推 SQL**：`ipo_code` / `doc_type` / `lang` / `embedding_dim` 全在 WHERE，多版本 embedding 隔离。**失败链路**：vector 失败 → 仅 BM25；BM25 失败 → 仅 vector；rerank 失败 → fallback RRF 顺序；空 query / 全标点 / dim mismatch 全有兜底。**ORM 不反映 tsv**（generated read-only），BE-S2-004 业务代码 0 改动。新增 21 条测试（19 hybrid_search + 2 schema 0004）。**关键 bug 自查**：head=0004 后 0003 downgrade 测试 `command.downgrade(cfg, "-1")` 不再退到 0002，改用显式 revision
- ✅ **BE-S2-006a**（Tool 注册中心 + 2 个最简工具）：新建 `app/services/agent/` 包 — `tool_registry.py`（`Tool` / `ToolResult` frozen dataclass + 模块级 `_REGISTRY` + register/get/list_all/list_openai_schemas/unregister/clear_registry_for_test）+ `sandbox.py`（`@sandboxed(input_model, timeout_seconds)` 装饰器：pydantic 入参校验 + asyncio.wait_for 超时 + 异常归一 + elapsed_ms 注入 + deps 透传）+ `tools/basic_info.py`（`get_ipo_basic_info` 对接 `ipo_service.get_ipo`, Decimal/date 序列化为 JSON 友好类型）+ `tools/financial.py`（`get_financial_statements` 对接 `ipo_service.get_ipo_detail.extra.financial_summary`，缺数据走 `warning` 而非 failure，让 LLM 决定是改 Tool 还是回答"暂无数据"）。**Tool 协议走 OpenAI tools schema**（DeepSeek-V3 / Qwen / GLM-4 全兼容）；**入参 schema = pydantic BaseModel + `model_json_schema()`**（一处定义 = 入参文档 + 入参校验 + LLM schema 三合一）；**side effect 注册**（加 Tool = 加文件 + 加 import 一行）；异常**绝不**冒上主循环（runner 内任何 exception 在沙盒兜底为 `ToolResult.failure`，仅露 `Exception.__class__.__name__` 给 LLM）。新增 46 条测试（tool_registry 15 + sandbox 12 + basic_info 8 + financial 11）。**关键 bug 自查**：① loguru 不通过 stdlib logging，pytest `caplog` 抓不到 → 改 `monkeypatch.setattr(logger, "warning", _capture)` 直接 patch；② `importlib.reload(tools_pkg)` 父包不重跑子模块 import → 改显式 reload `tools_pkg.basic_info` / `tools_pkg.financial` 子模块；③ pydantic `model_json_schema()` 默认带 `title` 字段，部分自托管 LLM (Qwen-2.5) 解析挑剔 → `Tool.to_openai_schema()` 主动 `params.pop("title", None)` 防御
- ✅ **BE-S2-007**（ReAct 主循环 + 引用源装配 + 合规端层 disclaimer）：自实现 410 行 `agent/graph.py` ReAct 主循环（**不引 LangGraph 库**：plan / act / reflect 3 节点固定 graph，自实现可控且不引二级依赖；接口预留, 后期需要分支决策时再切）+ `agent/citation.py` 引用源装配（chunk_id 去重 + 1-based [n] 编号 + 200 字 snippet 截断 + 越界 [n] 剔除）+ `agent/system_prompt.py` 动态 prompt（静态 `_BASE` 9 条合规红线 + 动态嵌入 `tool_registry.list_all()` 的 6 个 tool 描述 + 可选会话锚点 IPO code）+ `agent/persistence.py` 4 张表薄包装（session resume / 64 字 title 截断 / bogus session_id 兜底新建 / pending→ok 流转 / 4KB error 截断 / Decimal cost 透传）+ `app/api/v1/chat.py` 新建 `POST /v1/chat/diagnose` SSE 端层（5 类 AgentEvent → SSE event/data 帧；引用源装配后单帧 `sources` 事件；ensure_disclaimer 端层兜底；`forbidden_pattern_filter` 端层兜底；事务边界放在端层 / 错误分支不忘 commit 审计行）。**关键设计**：① **buffered delta + 终步回放**（中间步 plan 输出文字是 LLM 内部 reasoning 不该泄漏到用户 UI，全程 buffer 仅终步无 tool_call 时回放为 TokenDeltaEvent）；② **匿名也可调**（`get_optional_user` 不强制 JWT，让未注册用户能 trial 一轮 + BE-S2-008 配额接 IP 限流分支）；③ **citation 只对 hybrid_search 工具结果做**（spec/04 §3.3 C "引用必须来自检索结果"，其他 tool 走结构化数据自然语言提及）；④ **DB 事务边界放在端层**（`AsyncSession` 一路传，整次请求 = 1 个事务，错误分支也走 try/except commit 审计行）；⑤ **DISCLAIMER 自动追加**（不靠 LLM 自加，端层硬上一道闸门）。新增 54 条测试（citation 14 + system_prompt 5 + graph 18 单测 + persistence 12 + chat_diagnose 5 集成；465 passed 全绿）。**关键 bug 自查**：① SSE 帧分隔符是 `\r\n\r\n` 而非 `\n\n`（`sse_starlette` 走 HTTP 标准），测试 `_parse_sse` 工具改成"先 normalize 再 split"；② buffered delta 在中间步丢失（初版"先 yield delta 再判断 tool_calls"会泄漏 plan 思考，改"先全程 buffer 终步统一回放"经典 ReAct 约定）；③ `session_history_to_messages` 不能把 tool role 喂回 LLM（OpenAI tool role 必须紧跟 assistant tool_call 后，续聊单独喂触发 400），显式过滤 role=tool；④ `bogus session_id` 抛 NoResultFound 撑爆 SSE 流，改 `one_or_none()` + None 时 fallthrough 走"新建"路径
- ✅ **BE-S2-006b**（余下 3 Tool + hybrid_search Tool 化）：4 个新 Tool — `tools/peers.py`（`get_peer_comparison`：industry_l2 优先 → industry_l1 fallback 找 5 家可比公司；`Literal["PE","PB","ROE","GrossMargin","Revenue"]` 5 维 metrics，PE 直接列 + 其余从 `extra.financial_summary` 提；该行业第一只 → 空 peers + warning 而非 failure）+ `tools/sentiment.py`（`get_sentiment_summary` placeholder：Sprint 2 没接 BE-S3 文章源，返回固定 `counts={pos:0,neu:0,neg:0}` + `data_source_status="not_connected"` + warning，让 LLM 知道是数据源未接入而非调用失败）+ `tools/historical.py`（`get_historical_winning_rate`：industry/sponsor/year_range 三 optional 过滤聚合 `extra->>'one_lot_winning_rate'::numeric`；sponsor 走 PG `sponsors @> '["..."]'::jsonb`；status≠listed 排除；命中 0 / 全 NULL 都返回 success + warning；`first_day_performance: null` 留口 BE-S3 接 K 线源）+ `tools/hybrid_search.py`（包装 BE-S2-005 `services/rag/hybrid_search` 函数为 Tool；**deps 注入 session**：BE-S2-007 主循环传 session 时直接用，不传走 `get_session_factory()` 起临时 session 解耦层级；入参收窄到 5 个语义参数，调优参数走 settings 默认）。**6 个 Tool 全数注册到位**（basic_info + financial + peers + sentiment_placeholder + historical + hybrid_search），BE-S2-007 LangGraph ReAct 主循环可以起了。新增 38 条测试（sentiment 7 单测 + hybrid_search 9 单测 + peers 9 集成 + historical 13 集成）。**关键 bug 自查**：① `one_lot_winning_rate` 不是 IPO ORM 列而是 `extra` JSONB 内（BE-007 schema 设计），改走 raw SQL `extra->>...::numeric` 与 ipo_service / favorite_service 现有读路径对齐；② 集成测试 fixture 调用 `get_session_factory()` 拉到生产 DSN — 在 `tests/integration/conftest.py::patch_session_factory` 的 targets 列表追加 peers / historical / hybrid_search 3 个新模块（与 BE-S2-006a `ipo_service_mod` 同思路）

- ✅ **BE-S2-008**（Agent 配额管理）：滑动窗口 24h ZSET + Lua 原子（`ZREMRANGEBYSCORE+ZADD+EXPIRE+ZCARD` 单脚本一次 RTT）+ FREE 5/天 / VIP 无限 / 匿名 IP 2/天三档 + `app/services/agent/quota.py`（`check_quota` 进流前不计数 / `record_usage` 进流后扣 / VIP noop 不写 Redis / race 容忍） + `app/api/v1/chat.py` 入口前置闸门（`HTTPException(429, ChatQuotaExceededResponse)` + `Retry-After` header / FE 拿 status code 弹升级 modal） + `app/cache/redis_client.py` 给 `RedisClientProtocol` 加 3 个高层滑动窗口接口（不暴露 ZSET 原语，InMemory 走排序 list + asyncio.Lock 等价语义）。**关键设计**：① **滑动窗口而非固定窗口**（spec/04 §限流明确要求滑动；固定窗口边界突发 = 1 分钟拿 2× 配额）；② **check 与 record 分两步**（DB 异常不应错扣额，user_message 写库后才 record_usage 真扣）；③ **VIP 走 settings whitelist 兜底, 不引 vip_memberships 表**（Sprint 3 接订阅表只换 `_resolve_plan`，接口 + 调用方 0 改动）；④ **匿名走 IP key**（`X-Forwarded-For` 第一段 / `request.client.host` fallback / NAT 共享 IP 安全侧"宁紧勿松"）；⑤ **fail-open Redis**（异常 logger.warning 不阻塞业务，避免"Redis 挂导致全平台 Agent 不可用"）；⑥ **member = user_msg.message_id**（防 ZSET 同 member 同 score 不增 ZCARD 计数停滞）；⑦ **Lua 原子**（避免"读 ZCARD 再 ZADD"中间窗口被清的 race）；⑧ **429 body 结构**（`code = "agent_quota_exceeded"` 给 FE 判逻辑 + 人话 message 给 toast + quota 详情给升级 modal）。35 条新增测（12 滑窗 + 18 quota 单测 + 5 chat_diagnose 集成测），累计 500 个测试。
- ✅ **BE-S2-009**（评测集 80 条 + 离线评测脚手架）：`apps/api/evals/` 独立包（不打入运行时镜像）— `schema.py`（Pydantic v2 EvalCase / EvalCaseResult / RunReport, jsonl 读写 + dup id 校验 + frozen=True）+ `metrics.py`（`compute_recall@5` 子串大小写不敏感 + top5 截断 / `extract_atomic_facts` 中文带空格日期 `2018 年 9 月 20 日` + 双向币种顺序 `99 港元` / `港元 99` + 百分比 + 数字 / `compute_hallucination` 字符级 baseline，**仅算"答案抽出的硬事实在 citations snippet 池里没出现"占比**，与覆盖率正交）+ `judge.py`（LLM-as-judge：走 `eval_judge_model` 默认 DeepSeek-V3 / 可改 GPT-4o，强制 `response_format=json_object`，1-5 分 + rationale + hallucinated_facts；`parse_response` 容忍 ```json``` 围栏 + 越界分数 + 字段缺失）+ `runner.py`（**3 mode**：`keyword` 无 IO smoke / `retrieval` 真调 hybrid_search 算 recall / `end_to_end` retrieved → 自定义精简 prompt 直调 `llm_client.chat`（**故意不走 graph.run** 防污染 chat_sessions 表）+ 算幻觉 + 可选 judge；`asyncio.Semaphore` 并发控速防 LLM 限流）+ `reporter.py`（markdown + JSON 双输出，`by_category` 表格区分 `n/a` 与 `0%`，失败 case + 字符级幻觉 top 列表）+ `cli.py`（`--mode` / `--use-judge` / `--fail-below-recall` / `--fail-above-hallucination`，退出码 0/1/2 给 CI）+ `evals/dataset/sprint2_80q.jsonl`（**80 条 4 类 × 20**：basic / risk / peers / rag, 8 只港股 IPO 覆盖：腾讯 / 美团 / 阿里 / 小米 / 快手 / 心动 / 网易 / 新东方在线，每条带 `expected_keywords` + `ground_truth_facts` + `reference_answer`）+ `Makefile` 4 个 target（`eval-sprint2-smoke` / `-retrieval` / `eval-sprint2` / `-judge`）。**关键设计**：① **`evals/` 独立于 `app/`**（评测是 dev/CI 工具，不打入生产镜像）；② **三 mode 递进**（CI smoke 跑 keyword 不依赖 PG / LLM；本地 dev 跑 retrieval 验召回；预发跑 end_to_end + judge 全量）；③ **`compute_hallucination` 与 `ground_truth_facts` 解耦**（baseline 只看"答案中的硬事实是否被引用支撑"，避免把"该说没说"的覆盖率问题混进来）；④ **正则覆盖中文边界 case**（`2018 年 9 月 20 日` 带空格、`99 港元` 与 `港元 99` 双向 — 早期 regex 漏了一个就让 BASIC_001 跑不出召回）；⑤ **end_to_end 不走 `agent.graph.run`**（生产链路的 plan/act/reflect 会落 chat_sessions / chat_messages, 评测应该是只读, 走精简 prompt + `llm_client.chat` 直调）；⑥ **CLI 退出码 2 = 阈值不达**（区别于 1 = 脚本自身错, 给 QA-S2-002 CI 化复用）。新增 62 条单测（schema 22 + metrics 16 + judge 12 + runner 12，全 mock 不打外部 IO），累计 **347 passed + 215 skipped (DB 依赖类) = 562 collected, 0 failed**；ruff + mypy 增量 0 错（`evals/` + `app/core/config.py` 全绿）。
- ✅ **QA-S2-001**（Agent E2E 集成测试）：新增 `tests/integration/test_e2e_chat_diagnose.py` 5 条 e2e 用例（~750 行），与 BE-S2-007 单点测 + BE-S2-008 配额测**互补**——单点测验"协议契约 + 单一行为"，e2e 测"主链路串联 + 多点协同 + 跨 PR 边界"。**5 条用例**：① **金线 happy path**（注册 → seed IPO → ReAct 多 tool 串联：hybrid_search + basic_info → 引用源装配 → 续聊 history 注入 → DB 落 6 messages / 2 tool_calls / 3 token_usages 完整）；② **沙盒兜底**（底层 `ipo_service.get_ipo` 抛 RuntimeError → `ToolResult.failure` 透传 SSE `tool_call status=error`，**不**冒成 SSE `error` 整流中断；LLM 第二步看到 tool error 仍能给 fallback 回答）；③ **forbidden_pattern_filter 端到端**（LLM 输出"强烈推荐买入 / 必涨"→ 主循环替换为 `[已合规过滤]` 落 chat_messages.content + `ensure_disclaimer` 兜底追加免责声明）；④ **匿名 + IP 限流**（不带 token → user_id IS NULL；同 IP 第 2 次 429 + ChatQuotaExceededResponse + Retry-After header）；⑤ **max_steps 熔断**（LLM 持续 tool_calls + max_steps=2 → 最后一步主循环不传 tools，强制 LLM 给 final 收尾，chat_tool_calls 落 ≤2 条）。**mock 策略**：fake_streaming_llm（FIFO 队列脚本化多轮 LLM 响应）+ mock `hybrid_search` 上层（避免依赖真 BGE embedding）+ 真打 PG（`ipo_service` / `persistence.*`）+ `override_quota_settings`（monkeypatch quota.get_settings）。**关键 bug 自查**：① `ChatToolCall.started_at` 字段名错猜（实际 `created_at`）；② `ChatToolCall.error_summary` 字段名错猜（实际 `error_message`）；③ mypy `attr-defined` on `basic_info_tool_mod.ipo_service` (implicit reexport) → 改 dotted-string `monkeypatch.setattr("app.services.ipo_service.get_ipo", _boom)`。新增 5 条 e2e 集成测，累计 **567 passed + 215 skipped = 782 collected, 0 failed**；ruff + mypy 增量 0 错。
- ✅ **QA-S2-002**（RAG 评测集 CI 化 + 后端 lint/type baseline 清零）：项目第一份 GitHub Actions（`xgzh/.github/workflows/ci.yml`）—— 三段串联架构：**fast lane**（无依赖, ruff 0 / mypy 0 / pytest unit / eval-sprint2-smoke, ~2min, 所有 PR 必跑）→ **integration lane**（`pgvector/pgvector:pg16` + `redis:7-alpine` service container, alembic head → pytest tests/ 含 e2e + RAG schema, ~5min, needs:fast）→ **eval-retrieval lane**（条件触发：同仓库 PR + `SILICONFLOW_API_KEY` secret 双 gate, 跑 `evals.cli --mode retrieval` + 上传报告 artifact, baseline 阶段阈值 0.0 留 Sprint 3 corpus seed 后改 0.70, ~3min, needs:integration）。Makefile 加 `ci-smoke` / `ci-integration` 两 aggregate target 让本地一行预演 CI 行为。**关键收尾**：把后端 **ruff baseline 52 → 0** + **mypy strict baseline 25 → 0** 一次性清干净，CI 真正起防 regression 门禁作用——31 处项目惯例（FastAPI `Depends` / 异常命名 / `datetime as Date` / `Decimal as _D`）全局加 ignore 配置 + inline 注释解释，21 处 ruff `--fix` 自动修，1 处 B007 手工改；mypy 12 处补缺失类型注解（生产代码 `app/main.py` lifespan/middleware / `app/api/v1/agent.py` SSE generator / `app/services/favorite_service.py` user_id / `app/db/models/ipo.py` JSONB / `app/cache/decorators.py` _hash_args），11 处显式标 stub 边界 ignore（redis-py async client `Awaitable[T] | T` / SQLAlchemy 2.0 `Result.rowcount` / pandas `to_datetime`），1 处删 unused ignore，1 处 `_ = req` 变量名冲突修复。`evals/cli.py` 已有 `--fail-below-recall` / `--fail-above-hallucination` 退出码 2 阈值告警，本 PR 不动。**关键设计**：① 三段 job 串联而非并发 — fast 红时不浪费 integration ~5min PG service 启动开销；② eval-retrieval 双 gate（workflow 级 if 挡 fork PR + step 级 secret 检查）— fork PR / 主仓库未配 secret 都不让 CI 红；③ 阈值 baseline 设 0.0 而非 spec 目标 0.70 — corpus seed 脚本上线前真实召回@5 = 0，0.70 阈值会让 eval 永久红失去意义；④ 类型 ignore 优先 `# type: ignore[<具体 code>]` 而非 `cast(Any, ...)`， 最小破坏面。Sprint 2 后端 P0 + e2e + CI 门禁全数 ✅。
- ✅ **FE-S2-001**（AI 对话页 UI + Pinia store + SSE consume）：前端 Sprint 2 第一刀，把 BE-S2-007 `/api/v1/chat/diagnose` 6 类 SSE 事件（`start / delta / tool_call / sources / end / error`）+ BE-S2-008 配额 429 一次性消费起来。**4 文件改动**：① **`apps/mp/utils/sse.ts`** 三端 SSE 升级（H5 fetch / MP enableChunked / App 同 MP）— 加 Authorization 注入（`readAccessTokenSync`）+ HTTP statusCode + body 暴露给上层（H5 `!resp.ok` 读 body try-parse / MP `enableChunked` 模式 4xx/5xx 仍走 success → 检查 `res.statusCode` 抛 onError）+ 错误回调改两参 `onError(err, ctx={statusCode, body})`；② **`apps/mp/api/chat.ts`** 新建 SSE 客户端（与 `app/schemas/chat.py` TS-Pydantic 一一对齐 6 类 payload + 配额 schema）+ `chatDiagnoseStream(body, handlers)` 按 event 分发到 8 个回调（`onStart` / `onDelta` / `onToolCall` / `onSources` / `onEnd` / `onEndError` / `onAgentError` / `onStreamError`）+ `ChatQuotaError` / `ChatAuthError` 自定义异常仅在 HTTP 429 / 401 / 403 抛 + FastAPI `HTTPException(detail=...)` 双层结构兼容；③ **`apps/mp/stores/chat.ts`** 新增 Pinia 多轮会话 store（5 state：`messages` / `currentSessionId` / `currentIpoCode` / `phase: idle|pending|streaming|done|error` / `globalError`；5 actions：`setIpoContext` 切 IPO 自动 reset / `sendQuestion` append user + asst placeholder + 启 SSE / `retryLast` 删失败 asst 保留 user 复用 lastQuestion / `reset` / `dismissGlobalError`；session_id 由后端 `start` 事件回填后续自动衔接 → 多轮自动；不持久化历史，靠后端 `session_id` 续聊接口拉）；④ **`apps/mp/pages/ipo/agent.vue`** 重写多轮 chat UI（顶部三段：免责 banner / IPO 锚定 chip / 全局 banner（auth 红 / quota 金渐变）+ 主体 scroll-view：空态 4 条 quick prompts / user 蓝右气泡 / asst 深色左气泡含 tool_call 折叠卡（默认折叠仅 name+status badge+latency, 点开看 args/result_preview/error 的 JSON pretty）+ citations chip 列（FE-S2-003 加抽屉）+ 内嵌 error 条按 kind 分色 + 流式 ▋ 光标 / `thinking` 三点动画 + 底栏 input + 发送按钮 isStreaming 时禁用 + safe-area-inset 适配）。**错误兜底 4 类分级**：HTTP 429 quota → 顶部金色 banner + 升级 VIP modal 占位（FE-S2-004 实装支付）；HTTP 401/403 → 红色 banner + "重新登录"（流接口不做 silent refresh，避免中途换 token）；SSE event=error → asst 内嵌错误条 + 重试按钮（删失败 asst 复用 lastQuestion）；网络断 → 同上 kind=network。**关键设计**：① **错误分两层**（globalError 顶部 banner + message.error 气泡内嵌）— 视觉上"账号失效"和"上一次提问失败"是两件事；② **流期间不做 silent refresh**（一旦流建立 token 锁死, 中途换要 abort+重建对长流体验糟）— 流期间 token 失效 → 流正常结束 → store catch ChatAuthError → banner 引导登录 → 用户重登录后下次 sendQuestion 自然带新 token；③ **`retryLast` 删失败 asst 保留 user**（避免重试 = 再贴一遍我的问题的视觉重复, 类似微信"消息失败 → 红感叹号 → 重发"）；④ **tool_call 默认折叠**（args 几十行 JSON 默认展开会淹没 LLM 主回答, 折叠仍能 debug "卡在哪一步"）；⑤ **离页 `onUnload` 强 reset()**（避免"返回页发现上次会话还在 → 用户困惑", Sprint 3 加历史会话列表页时再持久化）；⑥ **`expandedToolCalls` Set 响应式坑**（Vue 3 ref 包 Set 不会自动追踪 add/delete, 每次 toggle 后 `new Set(...)` 替换引用强触发响应）。**测试 / 质量**：vue-tsc 0 错 / ESLint 0 错 / 端兼容 H5 + MP-WEIXIN + App 全覆盖。

- ✅ **FE-S2-002**（打字机 + Markdown 增量解析 + SSE abort + 停止生成）：把 FE-S2-001 chat 骨架里裸文本的 LLM 输出升级为 **Markdown 增量渲染**，解决 token 100/s 暴击导致 reflow 抖动的问题；顺手把 SSE 跨端 abort 实现，给用户加"停止生成"按钮（spec 写明 FE-S2-001 时延后到本 PR 一并做）；以及把 `[N]` 引用转成可点击 anchor，为 FE-S2-003 抽屉打入口。**7 文件改动**：① **`apps/mp/utils/markdown.ts`**（新建 +245）轻量自实现增量 markdown parser（**不引第三方依赖**：`@dcloudio/*` npm 体系受 yank 影响, 加 `marked` / `markdown-it` 撞同样问题；自实现 ~245 行覆盖 LLM 投研对话场景 90%+ — 段落 / heading h1-h6 / 无序 / 有序列表 / 引用 / 代码块 / 加粗 / 斜体 / 行内代码 / 链接 / **citation `[N]` 单独识别**：regex 区分 `[1]` 纯数字 vs `[text](url)` 链接 vs `[text]` 普通文本, 避免 `[研报](url)` 误识别；不支持表格 / 嵌套列表 / HTML 原生标签, YAGNI；流式中每次 delta 全文重 parse < 1ms / 帧）；② **`apps/mp/components/MarkdownRenderer.vue`**（新建 +281）跨端 markdown 渲染组件（**纯 `<view>` + `<text>` 渲染**, 不用 v-html / rich-text 因 MP-WEIXIN 事件冒泡有坑；Props `:blocks + :streaming`；Emits `citation-tap(idx)` + `link-tap(url)` 让父页决定行为；流式光标 ▋ 在最后一个非 hr / 非 code 块尾, 代码块内不嵌光标避免破坏对齐；citation chip 渲染为蓝色 outline + 底色让用户感知可交互）；③ **`apps/mp/utils/typewriter.ts`**（新建 +85）跨端打字机节流调度器（H5 用 `requestAnimationFrame` 60fps + 后台 tab 自动暂停；MP / App polyfill `setTimeout(16ms)` 精度差 ~1ms 但够用；多个 delta push 在 16ms 内合并到 1 次 commit, 避免 100 token/s 触发 100 次 markdown 重 parse + DOM diff；`drain()` 兜底流结束 / 错误 / cancel 时强制 flush 防最后几字符卡 buffer；done 后 push 直接 commit 绕过节流防边界丢字）；④ **`apps/mp/utils/sse.ts`**（升级 +120 -50）SSE abort 跨端实现 — 返回类型从 `Promise<void>` 改为 `StreamHandle = { done, abort }`；H5 用 `AbortController` + `fetch({signal})`, abort 后 `fetch.then` 抛 `AbortError` → `onError(message='aborted', statusCode=0)`；MP / App 用 `RequestTask.abort()`, fail 回调 `errMsg` 命中 `/abort|interrupt/i` → 同样 onError；abort 后**不调 onComplete** 让上层语义清晰区分"自然 end"vs"用户取消"；导出 `isAbortError(err)` 工具函数；用块作用域 `{ ... }` 包裹 H5 / MP 两端实现避免 TS 看到 `// #ifdef` / `// #ifndef` 内的 `const done` 双声明（条件编译是注释, TS 静态检查会同时看到两分支）；⑤ **`apps/mp/api/chat.ts`**（升级 +35 -8）`chatDiagnoseStream` 返回类型 `Promise<void>` → `ChatStreamHandle = StreamHandle`；新增 `onAbort?` handler；底层 `isAbortError` 命中时不当 stream error 处理；`done` promise 仍在 quota / auth 时 reject 与 v1 保持一致；⑥ **`apps/mp/stores/chat.ts`**（升级 +110 -15）chat store 接打字机 + cancel — 新 phase `cancelled`（用户主动停止生成的终态, 不弹错 banner）/ 新 ChatMessageError kind `cancelled`（partial content 为空时的占位"已停止生成"）/ 新非响应式 state `_activeHandle` + `_activeTypewriter`（仅 streaming 期间非空）/ 新 getter `canCancel = phase === 'streaming'`（pending 阶段不允许 cancel 防"流没起就 abort"）/ 新 action `cancelStream()` 调 `_activeHandle.abort()` 触发 `_onAbort`；`_onDelta` 改走 `_activeTypewriter.push(text)`, commit 回调 `_commitDelta` 同步更新 `m.content` + `m.parsedBlocks`（markdown parser 重 parse）— `m.parsedBlocks` 缓存让 MarkdownRenderer 直接吃 blocks 不需在模板里 `computed`；所有终态分支（`_onEnd / _onAgentError / _onEndError / _onStreamError / _onAbort / quota catch / auth catch`）都加 `_drainTypewriter()` 防最后几字符丢失；`reset()` 自动 abort 进行中的流防离页时流仍在跑；`retryLast()` 把 `cancelled` 也视为可重试（用户"停止 → 想再来一次"的常见诉求）；⑦ **`apps/mp/pages/ipo/agent.vue`**（升级 +60 -45）assistant 气泡 content 区从 `<text>{{ m.content }}</text>` 升级为 `<MarkdownRenderer :blocks="m.parsedBlocks ?? []" :streaming="m.streaming" @citation-tap @link-tap />`；底部 composer 流式时切红色"■ 停止"按钮调 `chat.cancelStream()`, 否则蓝色"发送"；citation tap 占位弹 `uni.showModal` 显示 snippet 预览（FE-S2-003 改 ActionSheet → 抽屉 → 原文 PDF）；link tap MP 不支持外跳, 复制 URL 到剪贴板 + toast；cancelled chip 显示灰色"⏹️ 已停止生成"+"重新生成"按钮；移除原 `.bubble-content` flex baseline + `.cursor` blink 样式（MarkdownRenderer 自带）。**关键设计 / 取舍**：① **不引 marked / markdown-it 而是自实现** — npm 体系受 yank 影响 + LLM 投研回答 markdown 子集很窄 + MP 不能 v-html 第三方 lib 输出 HTML 还得过 rich-text 事件冒泡 hack；② **每次 delta 全文重 parse 而非 incremental tail buffer** — LLM 单回合 ~1-3KB 全文 parse < 1ms / 帧, 16ms 帧节流后实际触发 ~3-10 次 / 秒, YAGNI；③ **Typewriter 不做"逐字 reveal 平滑动画"** — LLM token 实际流速本身就是 1-3 字 / delta 自然就有打字机视觉, 真做插值会让显示滞后于真实流流结束时还要"追平"反而割裂；④ **citation `[N]` 与 markdown 链接 `[text](url)` 在 parser 中区分** — citation 必须 `[...]` 内是纯数字 + `]` 之后不跟 `(`, 避免 `[研报](url)` 误识别为 `idx='研报'` 的 citation；⑤ **abort 用块作用域 `{ ... }` 隔离 H5 / MP 实现** — 类似 Rust `match` 模式, 每个 arm 独立 lexical scope 避免 TS `Cannot redeclare block-scoped variable`；⑥ **cancelStream 仅在 streaming 阶段生效** — pending 阶段流还没起 abort 是 no-op, UI 此时按钮仍显示"生成中…"灰禁用, 仅 streaming 显示红色"■ 停止"防误点；⑦ **partial content 在 cancel 时保留**（类似 ChatGPT 行为）— 仅 `m.content === ''` 时显示明显的"已停止生成" placeholder, 有 partial content 时显示 chip + 重新生成按钮让用户决定接受残段还是重 query；⑧ **`m.parsedBlocks` 缓存而非模板 `computed`** — store 的 `_commitDelta` 同步 parse 缓存到 message, 模板直接 `:blocks="m.parsedBlocks"` 不走 computed；终态后 blocks 不再变 vue diff 零开销。**测试 / 质量**：vue-tsc 0 错 / ESLint 0 错；H5 + MP-WEIXIN + App 全覆盖；单测留 QA-S2-003 一并做（vitest + happy-dom）。

下一步推荐 → **FE-S2-003（引用源 ActionSheet 抽屉，0.5d）**：citation chip 已挂 `@tap` 占位, 只剩接 ActionSheet → 抽屉组件 + 跳后端原文 PDF；或并行 **FE-S2-004**（VIP 升级支付通道 + 配额引导精修, 0.5d）也可以接上去, 两者无依赖。剩余战场：**FE-S2-003/004**（前端 2 PR, ~1d）。

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
| [08](./spec/08-sprint-1-backlog.md) | Sprint 1 PR-Ready Backlog（16 issue + Sprint 1.5 收尾包）|
| [09](./spec/09-sprint-2-backlog.md) | Sprint 2 PR-Ready Backlog（16 issue · AI Agent + RAG）|

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
