# 08 - Sprint 1 PR-Ready Backlog（Week 3-4）

> 适用范围：在 [First Slice](../README.md) 跑通 (`/healthz` + `/ipos` + `/agent/diagnose` SSE) 之后，正式进入 Sprint 1。
>
> **设计原则**
> 1. **每个 issue = 一个 PR**：尽量 < 1 天工作量，独立可合并
> 2. **依赖关系成线**：上面的合并完，下面的才开
> 3. **每个 issue 都给 Cursor Prompt 模板**：复制到对话框直接贴
> 4. **AC（验收）必须可机器验证**：单测 / curl / pytest 命令
> 5. **合规护栏不可绕开**：所有涉及 LLM 输出的 PR 必须经过 `forbidden_pattern_filter` + `ensure_disclaimer`

---

## 📋 总览

| ID | 类型 | 标题 | 估算 | 依赖 | 优先级 |
|----|------|------|:----:|------|:------:|
| INFRA-001 ✅ | infra | PostgreSQL 初始 schema（Alembic + pgvector） | 0.5d | - | P0 |
| INFRA-002 ✅ | infra | Redis cache 封装 + 限流装饰器 | 0.5d | - | P0 |
| BE-001 | backend | User 模型 + phone OTP 发送（dev 走 mock 短信） | 0.5d | INFRA-001, INFRA-002 | P0 |
| BE-002 | backend | OTP 校验 + 注册/登录 + JWT 颁发 | 0.5d | BE-001 | P0 |
| BE-003 | backend | JWT 中间件 + `current_user` 依赖 | 0.5d | BE-002 | P0 |
| BE-004 | backend | Refresh token + 黑名单 | 0.5d | BE-003 | P0 |
| BE-005 | backend | 微信小程序登录（code → openid → unionid → 绑定） | 1d | BE-002 | P0 |
| BE-006 | backend | 邀请码生成 + 绑定（注册时落入 referrer） | 0.5d | BE-002 | P0 |
| BE-007 | backend | IPO 表持久化 + AKShare 调度入库 | 1d | INFRA-001 | P0 |
| BE-008 | backend | `GET /ipos` 切回数据库 + 筛选 + Redis 缓存 | 0.5d | BE-007, INFRA-002 | P0 |
| BE-009 | backend | `GET /ipos/{code}` 字段聚合（HKEX/AKShare 多源 merge） | 0.5d | BE-008 | P0 |
| BE-010 | backend | 用户自选股表 + add/remove API | 0.5d | BE-003, BE-008 | P0 |
| BE-011 | backend | 推送 token 注册 + 设备表 | 0.5d | BE-003 | P1 |
| FE-001 | frontend | 登录页（手机号 OTP） + 微信小程序一键登录 | 1d | BE-002, BE-005 | P0 |
| FE-002 | frontend | Auth Pinia store + uni.request 拦截器 | 0.5d | FE-001 | P0 |
| FE-003 | frontend | 个人中心 + 设置 + VIP 入口（无支付） | 1d | FE-002 | P0 |
| FE-004 | frontend | 首页瀑布流 + 今日打新卡片 + 打新日历 | 1.5d | BE-008 | P0 |
| FE-005 | frontend | 新股详情页（关注按钮 + 招股要点） | 1d | BE-009, BE-010 | P0 |
| FE-006 | frontend | 自选列表 Tab | 0.5d | BE-010, FE-005 | P0 |
| QA-001 | qa | API 集成测试套件（pytest + httpx） | 1d | BE-009 | P0 |

**合计**：后端 8d + 前端 6d + 基础 2d = **16 PR / ~12-14 工作日**（双周内可完）

---

## 🧩 依赖图

```
INFRA-001 ─┬─ BE-001 ─ BE-002 ─┬─ BE-003 ─ BE-004
INFRA-002 ─┘                   ├─ BE-005
                               ├─ BE-006
                               └─ BE-007 ─ BE-008 ─ BE-009 ─┬─ BE-010 ─ FE-006
                                                             └─ FE-005
BE-002 ──────────────── FE-001 ─ FE-002 ─ FE-003
BE-008 ─ FE-004
BE-003 ──────── BE-011
```

---

## 🎯 详细 issue（按推荐合并顺序）

### INFRA-001 · PostgreSQL 初始 schema (Alembic + pgvector) ✅ DONE

**目标**：把 `spec/05` 的核心 DDL 落到代码，建立 Alembic 第一次 migration。

**改动文件**（已实装）
- `apps/api/pyproject.toml` 加 `alembic`, `sqlalchemy[asyncio]`, `asyncpg`, `pgvector`
- `apps/api/alembic.ini`
- `apps/api/alembic/env.py`（async-aware；URL 优先级 `-x url=` > `XGZH_TEST_DATABASE_URL` > `settings.database_url`）
- `apps/api/alembic/versions/0001_init_core_schema.py`
- `apps/api/app/db/base.py`（`Base`, `get_engine`, `get_session_factory`, `get_session`）
- `apps/api/app/db/models/__init__.py`、`_mixins.py`
- `apps/api/app/db/models/user.py`、`auth.py`、`invite.py`、`ipo.py`、`push.py`
- `apps/api/tests/conftest.py`、`tests/test_migrations.py`
- `apps/api/.env(.example)` 加 `DATABASE_URL`

**AC**
- [x] `uv run alembic upgrade head` 本地（`postgresql+asyncpg://xgzh:xgzh_dev_pass@localhost:5432/xgzh`）跑通
- [x] 创建 7 张表：`users`, `auth_sessions`, `ipos`, `ipo_documents`, `user_favorites`, `push_tokens`, `invite_codes`
- [x] 30 个索引、6 个外键、4 个唯一约束（`uq_users_phone` 等）就位
- [x] `ipo_documents.embedding` 为 `vector(1024)` + HNSW cosine 索引
- [x] 扩展 `pgcrypto`、`vector` 自动启用
- [x] `tests/test_migrations.py` 三条测试（upgrade / downgrade / idempotent）全绿
- [x] `pytest -q` 在无 `XGZH_TEST_DATABASE_URL` 时 6 passed / 3 skipped；有此 env 时 9 passed / 0 failed

**Cursor Prompt**（保留以便未来重做参考）

```
按 spec/05 §3 的 SQL DDL 与 .cursor/rules/40-database.mdc 的命名/类型规范，
为 apps/api 增加 SQLAlchemy 2.0 async 模型 + 第一次 Alembic migration。

要求：
1. 所有金额字段用 NUMERIC，不可用 FLOAT
2. 主键统一 UUID（gen_random_uuid()）
3. 软删用 deleted_at（不要级联）
4. 所有 timestamp 带 timezone
5. embedding 字段 vector(1024)
6. 写一个 tests/test_migrations.py 跑 upgrade head + downgrade base
7. 不要碰 app/main.py 之外的其他业务代码

参考表清单：users / auth_sessions / ipos / ipo_documents / user_favorites / push_tokens / invite_codes
```

---

### INFRA-002 · Redis cache 封装 + 限流装饰器 ✅ DONE

**改动文件**（已实装）
- `apps/api/app/cache/__init__.py` — 公开 API
- `apps/api/app/cache/redis_client.py` — `RedisClientProtocol` + `RealRedisClient`（Lua INCR+EXPIRE）+ `InMemoryRedisClient`（asyncio.Lock）+ singleton 注入器
- `apps/api/app/cache/decorators.py` — `@cached`、`@rate_limit`、`RateLimitExceeded`
- `apps/api/tests/test_cache.py` — 17 条用例

**AC**
- [x] `cached` 支持 JSON 序列化（`default=str` 兜底 Decimal/datetime）、key 自动 hash（`sha256(func_name+args+kwargs)[:16]`）
- [x] `rate_limit` 用 Lua 脚本 `INCR; if==1 then EXPIRE end` 一次原子（防双 INCR 之间窗口被遗忘 EXPIRE）
- [x] 提供 `InMemoryRedisClient`（dict + `asyncio.Lock`）给单测；并发 100 协程 INCR 测试得 1..100 无丢失
- [x] 真 Redis 烟测：`xgzh:cache:smoke:*` 与 `xgzh:rate:smoke:user:alice` 均按命名规范写入并被 `RateLimitExceeded` 正确触发
- [x] `pytest tests/test_cache.py` ⇒ 17 passed
- [x] 全套 `pytest -q` ⇒ 23 passed / 3 skipped (无 DB) ; 26 passed (有 DB)

**关键不变量**
- INCR 之后再 INCR **不会** 重置 TTL（防止限流被无限延期）
- Redis I/O 失败时 `@cached` 走原函数 + log warn（业务不挂）
- `@rate_limit` Redis 挂掉则 raise（关闸而非放行，更安全）
- 所有 key 走 `namespaced_key()` 自动加 `xgzh:` 前缀，统一命名空间

**Cursor Prompt**（保留以便重做参考）

```
为 apps/api 实现 Redis 缓存层，按 .cursor/rules/40-database.mdc 的 key 规范：
- 命名空间统一 "xgzh:"
- 提供 @cached(ttl_seconds, namespace) 装饰器
- 提供 @rate_limit(times, per_seconds, key_func) 装饰器（用 INCR + EXPIRE 原子）
- 单测覆盖 cache miss/hit、rate_limit 命中/通过
- 不要引入 fakeredis 包，自己写一个最小 InMemoryRedis（dict + asyncio.Lock）即可
```

---

### BE-001 · User 模型 + phone OTP 发送

**改动文件**
- `apps/api/app/services/user_service.py`
- `apps/api/app/services/otp_service.py`
- `apps/api/app/api/v1/auth.py`（`POST /auth/otp/send`）
- `apps/api/app/adapters/sms/__init__.py`
- `apps/api/app/adapters/sms/mock.py`（dev 环境直接打日志）
- `apps/api/app/adapters/sms/aliyun.py`（占位，标 TODO，下一 Sprint 接）
- `apps/api/tests/test_otp_send.py`

**AC**
- [ ] `POST /auth/otp/send`：body `{"phone": "+8613xxx"}`，60 秒内同一手机号最多 1 次
- [ ] OTP 6 位数字，存 Redis key `xgzh:otp:{phone}` TTL 5 分钟
- [ ] dev 环境 mock SMS：日志里能看到验证码（方便手动测试）
- [ ] 错误码：`429 too_many_requests`、`400 invalid_phone`

**Cursor Prompt**

```
在 apps/api/app 实现 OTP 发送：
- POST /api/v1/auth/otp/send {phone}
- 同手机号 60s 限流（用 INFRA-002 的 @rate_limit）
- OTP 写入 Redis key "xgzh:otp:{phone}"，TTL 5 分钟
- 实现 SMSAdapter 接口（aliyun 占位 + mock 实现）
- mock 实现里 logger.info(f"[MOCK SMS] {phone} -> {code}")
- 单测覆盖：成功发送、限流命中、手机号格式错误
- 不要碰前端
```

---

### BE-002 · OTP 校验 + 注册/登录 + JWT

**改动文件**
- `apps/api/app/api/v1/auth.py`（`POST /auth/login/phone`）
- `apps/api/app/services/auth_service.py`（OTP verify + 自动注册）
- `apps/api/app/security/jwt.py`（`create_access_token`, `create_refresh_token`）
- `apps/api/app/schemas/auth.py`
- `apps/api/tests/test_auth_login.py`

**AC**
- [ ] `POST /auth/login/phone {phone, code}`：OTP 正确就返回 `{access_token, refresh_token, user}`
- [ ] 用户不存在则自动注册（写入 users 表）
- [ ] OTP 一次有效，校验后立即从 Redis 删除（防重放）
- [ ] access_token 30min / refresh_token 30 天，HS256，secret 来自 `.env`
- [ ] 单测：新用户登录、老用户登录、错误 OTP、过期 OTP

**Cursor Prompt**

```
在 BE-001 之上实现 OTP 校验与 JWT 颁发：
- POST /api/v1/auth/login/phone {phone, code}
- 校验通过后从 Redis 删除 OTP（一次性）
- 用户不存在则插入 users（设置 phone, created_at, last_login_at）
- 颁发 access(30m, HS256) + refresh(30d) JWT
- secret 从 settings.jwt_secret 读取（pyproject 里没有就加，default 必须给一个 dev 占位 + 警告日志）
- 必须有 4 个单测：新用户、老用户、错码、过期码
```

---

### BE-003 · JWT 中间件 + `current_user` 依赖

**改动文件**
- `apps/api/app/security/deps.py`（`get_current_user`, `get_optional_user`）
- `apps/api/app/api/v1/me.py`（`GET /me`）
- `apps/api/tests/test_me.py`

**AC**
- [ ] `Authorization: Bearer xxx` 解析；过期/无效返回 401
- [ ] `get_current_user` 强校验，`get_optional_user` 允许未登录
- [ ] `GET /api/v1/me` 返回当前用户基本信息
- [ ] 单测覆盖：合法 token / 过期 / 无效签名 / 缺失 header

---

### BE-004 · Refresh token + 黑名单

**改动文件**
- `apps/api/app/api/v1/auth.py`（`POST /auth/refresh`、`POST /auth/logout`）
- `apps/api/app/security/blacklist.py`（基于 Redis）
- `apps/api/tests/test_refresh.py`

**AC**
- [ ] `POST /auth/refresh {refresh_token}` 返回新 access_token
- [ ] `POST /auth/logout` 把 refresh_token 加入黑名单（TTL = 剩余有效期）
- [ ] 黑名单中的 refresh 拒绝刷新（401）

---

### BE-005 · 微信小程序登录

**改动文件**
- `apps/api/app/api/v1/auth.py`（`POST /auth/login/wechat-mp`）
- `apps/api/app/adapters/wechat/__init__.py`
- `apps/api/app/adapters/wechat/mp_login.py`（调 `code2Session`）
- `apps/api/.env.example` 加 `WECHAT_MP_APP_ID` / `WECHAT_MP_APP_SECRET`
- `apps/api/tests/test_wechat_login.py`（用 `respx` mock 微信 API）

**AC**
- [ ] `POST /auth/login/wechat-mp {code}` → 调 `https://api.weixin.qq.com/sns/jscode2session` → 拿 openid/unionid
- [ ] 用户表新增 `wechat_unionid` 字段索引
- [ ] 已绑定 unionid 的用户直接登录；新用户自动注册
- [ ] respx 单测：mock 成功 / mock 失败（微信返回 errcode）

---

### BE-006 · 邀请码

**改动文件**
- `apps/api/app/services/invite_service.py`
- `apps/api/app/api/v1/invite.py`（`POST /invite/bind`）
- `apps/api/app/utils/short_id.py`（base62 短码）
- `apps/api/tests/test_invite.py`

**AC**
- [ ] 用户注册成功自动生成 6 位 base62 邀请码（写入 invite_codes 表）
- [ ] `POST /invite/bind {code}` 在登录态下绑定 referrer（一次性，已绑定拒绝）
- [ ] 自己不能绑自己（400）
- [ ] 单测：生成唯一性、绑定成功、自绑、重复绑

---

### BE-007 · IPO 表持久化 + 调度

**改动文件**
- `apps/api/app/services/ipo_ingest_service.py`（akshare → upsert）
- `apps/api/app/scheduler/__init__.py`（用 `APScheduler` AsyncIOScheduler）
- `apps/api/app/main.py`（lifespan 启动 scheduler）
- `apps/api/tests/test_ipo_ingest.py`

**AC**
- [ ] 启动时立即跑一次 ingest（dev 用 5 条样本）
- [ ] 每天 08:00 / 20:00（Asia/Shanghai）跑全量更新
- [ ] upsert 按 `code` 唯一约束，不重复
- [ ] 失败有重试 + 错误告警日志
- [ ] HK 暂仍用 seed（标 TODO，Sprint 2 接 HKEX）

**Cursor Prompt**

```
为 apps/api 引入 APScheduler，把 fetch_a_ipos 的结果落到 ipos 表（upsert）。
要求：
- lifespan 启动 AsyncIOScheduler，应用退出时优雅关闭
- 立即跑一次 + 每天 08:00、20:00 Asia/Shanghai
- 写一个 IPOIngestService.upsert(items) 方法
- 失败时 logger.error 但不让进程崩溃
- 单测：mock fetch_a_ipos 返回 [item1, item2]，调用 upsert，再调用一次确认是 update 不是 insert
- HK 数据仍用 seed，加 TODO 注释指向 spec/04 §4
```

---

### BE-008 · `GET /ipos` 切回数据库 + Redis 缓存

**改动文件**
- `apps/api/app/services/ipo_service.py`（彻底改用 DB 查询）
- `apps/api/app/api/v1/ipos.py`（加 `industry`, `status`, `listing_date_from/to` 筛选）
- `apps/api/tests/test_ipos_list.py`

**AC**
- [ ] 列表查询走 DB，不再每次打 akshare
- [ ] 加 5 分钟 Redis 缓存（key 包含所有筛选参数）
- [ ] 支持分页 `limit` / `offset`，默认 20，max 100
- [ ] 响应时间 P95 < 100ms（缓存命中）
- [ ] 单测：分页、筛选、缓存命中

---

### BE-009 · `GET /ipos/{code}` 字段聚合

**改动文件**
- `apps/api/app/services/ipo_service.py`（`get_ipo_detail` 多源 merge）
- `apps/api/app/schemas/ipo.py`（新增 `IPODetail` 包含财务摘要、保荐人、亮点）

**AC**
- [ ] `GET /api/v1/ipos/{code}` 返回详情（含财务摘要 / 保荐人 / 亮点 / 风险）
- [ ] 不存在返回 404
- [ ] 缓存 30 分钟

---

### BE-010 · 用户自选股 + API

**改动文件**
- `apps/api/app/services/favorite_service.py`
- `apps/api/app/api/v1/favorites.py`（`POST/DELETE/GET /favorites`）
- `apps/api/tests/test_favorites.py`

**AC**
- [ ] `POST /favorites {code}` 添加；重复返回 200 不报错
- [ ] `DELETE /favorites/{code}` 移除
- [ ] `GET /favorites` 返回用户全部自选 IPO（带最新行情字段）
- [ ] 必须登录态（401 if no token）

---

### BE-011 · 推送 token 注册（P1）

**改动文件**
- `apps/api/app/api/v1/push.py`
- `apps/api/app/services/push_service.py`

**AC**
- [ ] `POST /push/tokens {platform, token}` 写 push_tokens 表
- [ ] 同 user + platform 唯一（覆盖）
- [ ] 不实际推送（推送实施排到 Sprint 4）

---

### FE-001 · 登录页（手机 OTP + 微信一键）

**改动文件**
- `apps/mp/pages/auth/login.vue`
- `apps/mp/pages.json`（新路由）
- `apps/mp/api/auth.ts`

**AC**
- [ ] H5 / 小程序双端：手机号 + 验证码登录
- [ ] 小程序额外提供微信一键登录按钮
- [ ] 验证码 60 秒倒计时
- [ ] 必含合规 footer：`登录即同意《用户协议》《隐私政策》《免责声明》`

---

### FE-002 · Auth Pinia store + 拦截器

**改动文件**
- `apps/mp/stores/auth.ts`
- `apps/mp/utils/request.ts`（加 Authorization 自动注入 + 401 自动跳登录）

**AC**
- [ ] `useAuthStore()` 暴露 `user`, `accessToken`, `login()`, `logout()`, `refresh()`
- [ ] uni.storage 持久化 token
- [ ] 401 时尝试一次 refresh，失败再跳登录页

---

### FE-003 · 个人中心

**改动文件**
- `apps/mp/pages/me/index.vue`
- `apps/mp/pages/me/settings.vue`

**AC**
- [ ] 头像 / 昵称 / VIP 状态展示（VIP=未开通占位）
- [ ] 设置项：消息提醒、暗黑模式、清缓存、退出登录
- [ ] 退出登录调 `/auth/logout` 并清本地

---

### FE-004 · 首页瀑布流 + 打新日历

**改动文件**
- `apps/mp/pages/index/index.vue`（重写为分区式）
- `apps/mp/components/IPOCard.vue`
- `apps/mp/components/IPOCalendar.vue`

**AC**
- [ ] 首屏 3 个分区：今日打新 / 即将打新 / 近期上市
- [ ] 横向滑动的 7 日打新日历
- [ ] 卡片点击跳详情
- [ ] 必含 footer 免责（保留）

---

### FE-005 · 新股详情页增强

**改动文件**
- `apps/mp/pages/ipo/detail.vue`（在现有基础上扩展）
- `apps/mp/components/FavoriteButton.vue`

**AC**
- [ ] 顶部基本信息卡 + 关注按钮（接 BE-010）
- [ ] 财务摘要 / 保荐人 / 亮点 / 风险 4 个 tab
- [ ] AI 诊断按钮（已有）保留并加 VIP 配额提示
- [ ] 必含 IPO 风险提示 banner

---

### FE-006 · 自选列表 Tab

**改动文件**
- `apps/mp/pages/me/favorites.vue`

**AC**
- [ ] 列表显示用户全部自选
- [ ] 长按移除（带二次确认）
- [ ] 空态有引导文案

---

### QA-001 · API 集成测试套件

**改动文件**
- `apps/api/tests/integration/conftest.py`（pytest fixture：临时 DB + Redis）
- `apps/api/tests/integration/test_e2e_ipo_diagnose.py`

**AC**
- [ ] 一条 e2e 用例：注册 → 拿 token → /ipos → /agent/diagnose → 校验 SSE 帧 + 免责声明
- [ ] CI 上能用 docker-compose 起 PG/Redis 跑通
- [ ] 失败时 dump 完整 server log 到 artifact

---

## 🚦 Definition of Ready（开 PR 前）

每个 issue 开 PR 前要满足：
- [ ] 关联的 spec/* 文档我读过
- [ ] `.cursor/rules/*.mdc` 我懂（特别是 40-database / 50-compliance）
- [ ] DB schema 改动跑过 `alembic upgrade head` 和回滚
- [ ] 至少 1 个单测（happy path）+ 1 个失败用例

## ✅ Definition of Done（合并前）

- [ ] `uv run pytest` 全绿（最终 16 个 PR 都合并完时累计 ≥ 60 个测试）
- [ ] `curl` 文档命令在 README 里能跑通
- [ ] 改了 schema 的，alembic migration 文件 review 过（不 squash 已生成的版本）
- [ ] 没引入 mypy 报错（`uv run mypy app`）
- [ ] 没引入 ruff 报错（`uv run ruff check`）
- [ ] 涉及 LLM 输出的 PR：`forbidden_pattern_filter` / `ensure_disclaimer` 必须在路径上
- [ ] 涉及金额：必须 `Decimal`（后端）/`big.js`（前端）

## 🛡 Sprint 1 不能碰的事

- 真实短信短信网关（用 mock，aliyun adapter 占位 TODO）
- 真实微信支付（Sprint 3 才上）
- iOS 上架（Sprint 5）
- 真实 LLM Key 写到代码里（永远走 `.env`）

## 📦 Sprint 1 完成后的产出物

- 用户可在 H5 / 小程序登录
- 首页能看到当日 IPO 列表 + 打新日历（数据来自 PG，不再每次打 akshare）
- 详情页能关注新股
- AI 诊断仍可用（依赖 LLM Key 配置）
- 16 PR + 60+ 测试 + 7 张 DB 表 + 1 个调度任务

> 然后进入 Sprint 2（AI Agent + RAG），spec/07 里有完整任务，需要时再拆 PR。
