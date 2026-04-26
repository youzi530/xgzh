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
- **后端测试**：
  - 无 DB：`cd apps/api && uv run pytest -q` ⇒ 89 passed / 119 skipped
  - 有 DB：`XGZH_TEST_DATABASE_URL=... uv run pytest -q` ⇒ 208 passed

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
