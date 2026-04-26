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
| BE-001 ✅ | backend | User 模型 + phone OTP 发送（dev 走 mock 短信） | 0.5d | INFRA-001, INFRA-002 | P0 |
| BE-002 ✅ | backend | OTP 校验 + 注册/登录 + JWT 颁发 | 0.5d | BE-001 | P0 |
| BE-003 ✅ | backend | JWT 中间件 + `current_user` 依赖 | 0.5d | BE-002 | P0 |
| BE-004 ✅ | backend | Refresh token + 黑名单 | 0.5d | BE-003 | P0 |
| BE-005 ✅ | backend | 微信小程序登录（code → openid → unionid → 绑定） | 1d | BE-002 | P0 |
| BE-006 ✅ | backend | 邀请码生成 + 绑定（注册时落入 referrer） | 0.5d | BE-002 | P0 |
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

### BE-001 · User 模型 + phone OTP 发送 ✅ DONE

**目标**：实现 `POST /api/v1/auth/otp/send`，60 秒限流 + 5 分钟 OTP，dev 走 mock SMS 把验证码打到日志。

**改动文件**（已实装）
- `apps/api/app/utils/phone.py`（新）：E.164 归一化 + 5 国家码白名单（+86/+852/+853/+65/+886）+ `mask_phone` 脱敏
- `apps/api/app/adapters/sms/`（新目录）
  - `base.py`：`SMSAdapter` Protocol、`SMSDeliveryError`、`SMSSendResult` dataclass
  - `mock.py`：`MockSMSAdapter`（dev 用，把 `phone+code` 打到 loguru）
  - `aliyun.py`：`AliyunSMSAdapter` 占位（构造参数齐全，`send_otp` 抛 `NotImplementedError`，Sprint 2 接）
  - `factory.py`：`get_sms_adapter()` / `set_sms_adapter()` / `reset_sms_adapter()` singleton
- `apps/api/app/services/otp_service.py`（新）：`generate_otp_code` (`secrets.randbelow`)、`store_otp` / `fetch_stored_otp` / `consume_otp`、`send_otp` 编排（失败回滚 Redis）
- `apps/api/app/services/user_service.py`（新骨架）：`find_user_by_phone` / `find_user_by_id`（BE-002 用）
- `apps/api/app/schemas/auth.py`（新）：`OTPSendRequest` / `OTPSendResponse`
- `apps/api/app/api/v1/auth.py`（新）：`POST /auth/otp/send`，`@rate_limit(times=1, per_seconds=60, namespace="otp_send", key_func=已归一化phone)`
- `apps/api/app/api/v1/__init__.py`：注册 `auth.router`
- `apps/api/app/main.py`：全局 `RateLimitExceeded → 429` handler（带 `Retry-After` header）
- `apps/api/app/core/config.py`、`.env(.example)`：加 `SMS_ADAPTER`、`ALIYUN_SMS_*`、`OTP_TTL_SECONDS`、`OTP_RESEND_INTERVAL_SECONDS`
- `apps/api/tests/test_otp_send.py`（新，32 用例）

**AC**
- [x] `POST /auth/otp/send`：body `{"phone": "+8613xxx"}`，60 秒内同一手机号最多 1 次
- [x] OTP 6 位数字（`secrets.randbelow`），存 Redis key `xgzh:otp:{phone}` TTL 5 分钟
- [x] dev 环境 mock SMS：日志里能看到验证码（`[MOCK SMS] to=+86xxx code=123456`）
- [x] 错误码：`429 too_many_requests`（带 `Retry-After: 60`）、`400 invalid_phone`、`502 sms_delivery_failed`（通道挂掉）
- [x] 限流 key 用 **归一化后** 的 phone：`13800138000` 与 `+8613800138000` 共享同一限流桶
- [x] SMS 通道失败时，自动清掉刚存的 OTP（避免脏数据）
- [x] 32 用例全过；全套 58/58 通过；smoke test 1+86/1+852 / 429 / 400 / 502 全部命中预期

**Smoke 命令**

```bash
# 启动 (PYTHONUNBUFFERED=1 让 loguru 不被块缓冲)
PYTHONUNBUFFERED=1 uv run uvicorn app.main:app --port 8000

# 200
curl -i -X POST localhost:8000/api/v1/auth/otp/send \
  -H 'content-type: application/json' -d '{"phone":"13800138000"}'

# 429 (60s 内重复)
curl -i -X POST localhost:8000/api/v1/auth/otp/send \
  -H 'content-type: application/json' -d '{"phone":"+8613800138000"}'

# 400
curl -i -X POST localhost:8000/api/v1/auth/otp/send \
  -H 'content-type: application/json' -d '{"phone":"+1234567890"}'

# 验证 Redis
redis-cli get  'xgzh:otp:+8613800138000'        # 6 位 OTP
redis-cli ttl  'xgzh:otp:+8613800138000'        # ≤ 300
redis-cli get  'xgzh:rate:otp_send:phone:+8613800138000'  # 1
```

---

### BE-002 · OTP 校验 + 注册/登录 + JWT ✅ DONE

**目标**：实现 `POST /api/v1/auth/login/phone`，OTP 一次性消费 + 自动注册 + 颁发 access/refresh 双 token。

**改动文件**（已实装）
- `apps/api/app/security/`（新目录）
  - `jwt.py`：`create_access_token` / `create_refresh_token` / `decode_token`，HS256，强制校验 `iss/aud/sub/typ/jti/iat/exp`，过期单独抛 `TokenExpiredError`
  - `__init__.py`：导出 `ACCESS_TOKEN_TYPE` / `REFRESH_TOKEN_TYPE` / `*Payload` / 异常等
- `apps/api/app/services/auth_service.py`（新）：
  - `verify_phone_login` 编排（OTP 常量时间比较 → consume → find_or_create_user → touch last_active → 颁发双 token）
  - `find_or_create_user_by_phone`、`_create_user_with_phone` 含 invite_code 冲突重试 + phone 并发注册降级
- `apps/api/app/services/user_service.py`：BE-001 已加的 `find_user_by_phone` 直接复用
- `apps/api/app/schemas/auth.py`：`PhoneLoginRequest` / `TokenPair` / `UserPublic` / `LoginResponse`
- `apps/api/app/api/v1/auth.py`：`POST /auth/login/phone`，`@rate_limit(5, 300, namespace="otp_verify")`
- `apps/api/app/core/config.py` + `.env(.example)`：加 `JWT_SECRET` / `JWT_ALGORITHM` / `JWT_ISSUER` / `JWT_AUDIENCE` / `JWT_ACCESS_TTL_SECONDS` / `JWT_REFRESH_TTL_SECONDS` / `OTP_VERIFY_MAX_ATTEMPTS`
- `apps/api/pyproject.toml`：加 `pyjwt>=2.8.0`
- `apps/api/tests/test_auth_login.py`（新，11 用例，依赖 PG）：新用户 / 老用户 / 错码 / 过期 / 一次性 / 错码不消费 / 限流 / token 可解 / typ 隔离
- `apps/api/tests/test_jwt.py`（新，10 用例，纯单元）：签名 / aud / iss / typ / 过期 / 篡改 / sub UUID / jti 唯一

**AC**
- [x] `POST /auth/login/phone {phone, code}` 正确时返回 `{user, tokens, is_new_user}`
- [x] 用户不存在则自动注册（写 `users.phone`、`invite_code`、`status=1`、`region=CN`、`last_active_at`）
- [x] OTP 一次有效（成功登录后立刻 `consume_otp`，错码不消费让用户在 5/5min 限流内可重试）
- [x] access 30min / refresh 30 天，HS256，secret 来自 `.env`；带 `iss/aud/typ/jti`，typ 严格隔离
- [x] OTP 校验用 `hmac.compare_digest` 常量时间比较，避免侧信道
- [x] verify 限流：5 次/5min，与 send 限流分桶（`namespace="otp_verify"` vs `"otp_send"`）
- [x] 错误码：`401 otp_invalid` / `401 otp_expired` / `400 invalid_phone` / `429 too_many_requests`
- [x] 单测覆盖：11 端到端 + 10 纯 JWT 单元，全套 79/79 PASS（含 PG）

**关键设计决策**
1. **OTP 错码不消费**：用户输错码不应让 OTP 立刻失效（否则用户得等 60s 重发限流），但错码会计入 verify 限流计数。
2. **invite_code 冲突重试**：8 字符 (大写+数字) 空间足够大，但仍 5 次重试兜底；phone 唯一约束冲突时降级为 fetch（处理并发注册）。
3. **JWT typ 隔离**：access / refresh 在 `decode_token` 时都强制对比 `expected_type`，避免 refresh 被当 access 用绕过过期。
4. **dev secret 警告**：`jwt.py._warn_if_dev_secret` 在非 dev 环境检测到占位 secret 或长度 < 32 时打 ERROR 日志，但不 raise（避免线上突然 500）。
5. **session 事务边界**：service 显式 commit，路由层 `get_session` dep 仅在异常时 rollback，事务粒度由业务层把控。

**Smoke 命令**

```bash
PYTHONUNBUFFERED=1 uv run uvicorn app.main:app --port 8000

# 1. send + 拿 OTP (dev mock)
curl -X POST localhost:8000/api/v1/auth/otp/send \
  -H 'content-type: application/json' -d '{"phone":"13800138000"}'
CODE=$(redis-cli get 'xgzh:otp:+8613800138000')

# 2. login -> 200 + access/refresh
curl -X POST localhost:8000/api/v1/auth/login/phone \
  -H 'content-type: application/json' -d "{\"phone\":\"13800138000\",\"code\":\"$CODE\"}"

# 3. 复用同一 OTP -> 401 otp_expired
curl -X POST localhost:8000/api/v1/auth/login/phone \
  -H 'content-type: application/json' -d "{\"phone\":\"13800138000\",\"code\":\"$CODE\"}"
```

---

### BE-003 · JWT 中间件 + `current_user` 依赖 ✅ DONE

**改动文件**
- `apps/api/app/security/deps.py`（手写 `Authorization` 解析 + `get_current_user` / `get_optional_user`）
- `apps/api/app/security/__init__.py`（导出依赖）
- `apps/api/app/api/v1/me.py`（`GET /api/v1/me`）
- `apps/api/app/api/v1/__init__.py`（注册 `me.router`）
- `apps/api/tests/test_me.py`（14 个端到端用例）

**AC**
- [x] `Authorization: Bearer xxx` 解析；区分 `token_missing` / `token_scheme_invalid` / `token_invalid` / `token_expired` 四种 401 reason
- [x] `get_current_user` 强校验：缺 token / scheme 错 / 签名错 / 过期 / typ 不是 `access` / sub 用户不存在 / 用户被禁用 → 全部 401，并通过 `WWW-Authenticate: Bearer` header 通告
- [x] `get_optional_user` 允许未登录：无 header / 解析失败 / 用户不存在 都返回 `None`，业务层自行决定是否提供匿名能力
- [x] `GET /api/v1/me` 返回 `UserPublic`（user_id / nickname / avatar / region / invite_code / status / created_at）；**不含 phone** 等敏感字段
- [x] 严格的 `typ` 隔离：refresh token 复制到 `Authorization` 也会被 401（`token typ mismatch`）
- [x] 单测覆盖：合法 token / 过期 / 篡改 / 错误 scheme / 缺 header / refresh 当 access / 用户被软删 / 用户禁用 / sub UUID 不存在 / get_optional_user 各路径 — 全 14 用例通过

**关键设计**
1. **不复用 FastAPI 的 `HTTPBearer(auto_error=False)`**：它把"无 header"和"非 Bearer scheme"折叠成同一个 `None`，丢失诊断信号。我们手动解析 `Authorization` header，给前端不同 reason 让 UX 可以分流（过期 → silent refresh，无效 → 跳登录）。
2. **解 token 后必查 DB 一次**：token 内的 `status` 字段不可信，被禁用的用户即使握着合法 token 也会被 401（`user_disabled`），软删用户走 `user_not_found`。
3. **typ 强制匹配**：`decode_token(..., expected_type=ACCESS_TOKEN_TYPE)` 在 deps 层兜底，跟 `app.security.jwt` 的 `typ` 校验形成双保险。

**烟测命令**
```bash
# 用本地 Postgres 起服务
cd apps/api && PYTHONUNBUFFERED=1 uvicorn app.main:app --host 127.0.0.1 --port 8001

# 1. 缺 header → 401 token_missing
curl -i localhost:8001/api/v1/me

# 2. 非 Bearer scheme → 401 token_scheme_invalid
curl -i -H 'Authorization: Basic dXNlcjpwYXNz' localhost:8001/api/v1/me

# 3. 走完一遍登录 → 拿 access → /me 200
curl -X POST localhost:8001/api/v1/auth/otp/send \
  -H 'content-type: application/json' -d '{"phone":"13900139003"}'
# 从日志拿 OTP, 然后:
curl -X POST localhost:8001/api/v1/auth/login/phone \
  -H 'content-type: application/json' \
  -d '{"phone":"13900139003","code":"<OTP>"}'  # 拿 access_token / refresh_token

curl -i -H "Authorization: Bearer <access>" localhost:8001/api/v1/me   # 200
curl -i -H "Authorization: Bearer <refresh>" localhost:8001/api/v1/me  # 401 token typ mismatch
```

---

### BE-004 · Refresh token + 黑名单 ✅ DONE

**改动文件**
- `apps/api/app/security/blacklist.py`（新增，Redis SETEX，TTL=token 剩余有效期）
- `apps/api/app/security/__init__.py`（导出 `blacklist_jti` / `is_jti_blacklisted`）
- `apps/api/app/security/deps.py`（`_resolve_user_from_token` 加 jti 黑名单检查 → 401 `token_revoked`）
- `apps/api/app/services/auth_service.py`（`refresh_tokens` rotation + `revoke_access_token` + `revoke_refresh_token` + 4 个错误类型）
- `apps/api/app/schemas/auth.py`（`RefreshRequest` / `LogoutRequest` / `LogoutResponse`）
- `apps/api/app/api/v1/auth.py`（`POST /auth/refresh` + `POST /auth/logout`）
- `apps/api/tests/test_refresh.py`（17 个端到端用例）

**AC**
- [x] `POST /auth/refresh {refresh_token}` 返回新 **access + refresh**（rotation，不是只换 access）
- [x] `POST /auth/logout` 拉黑当前 access 的 jti（**TTL=剩余有效期**）；body 带 `refresh_token` 时连同拉黑（且 sub 必须与 `current_user` 一致，**拒绝拉黑别人的 token**）
- [x] 黑名单中的 refresh 拒绝刷新 → 401 `token_revoked`
- [x] 黑名单中的 access 拒绝访问任何受保护接口 → 401 `token_revoked`（`get_current_user` 兜底）
- [x] refresh 限流：同一 refresh_token 1 分钟 5 次（`namespace="token_refresh"`），第 6 次 429
- [x] Redis 失败时 `is_jti_blacklisted` **fail-open**（避免单点故障导致全员 401），`blacklist_jti` 失败抛错（"以为已登出但其实没"是更严重的错觉）
- [x] 单测覆盖：refresh happy / chain / 旧 refresh 重放 / access 当 refresh / 篡改 / 过期 / 用户禁用 / logout 拉黑 access+refresh / logout 不带 body / logout 不带 Authorization / **logout 别人的 refresh 拒绝**（关键安全测试） / 限流 / blacklist 单元 / fail-open

**关键设计**

1. **Refresh Rotation**：每次 refresh 都拉黑旧 refresh 的 jti，**颁发新 access + 新 refresh**。这是工业界默认做法（OAuth2 RFC 6749 §10.4 推荐）；旧 refresh 被中间人复制后第二次也用不了。
2. **Logout 双拉黑**：access TTL 短（30min），但用户登出后立即生效是基本预期，所以 access 也必须拉黑而不是"等过期"。`get_current_user` 在解 token 后查一次 `is_jti_blacklisted` 实现这个。
3. **黑名单粒度=jti 而非 user_id**：同一用户多设备并存时，登出"这台手机"不该影响 PC 端登录。"踢全员"是 BE-011 之后的 `user_token_epoch` 机制，不在本 PR 内。
4. **Fail-open vs fail-close**：黑名单查询失败 → 放行（业务可用）；黑名单写入失败 → 抛错（防止"以为登出了"的安全错觉）。两边方向不对称，但符合实际威胁模型。
5. **Access TTL 设计**：30min 是黑名单存量上限的关键控制器。即便 logout 风暴打来，单条目最多存活 30min，Redis 内存压力可控。

**烟测命令**
```bash
# 0. 起服务
cd apps/api && PYTHONUNBUFFERED=1 uvicorn app.main:app --host 127.0.0.1 --port 8001

# 1. 走完登录拿 access/refresh (略, 见 BE-002)

# 2. refresh -> 200 + 新 access/refresh, 旧 refresh 被拉黑
curl -X POST localhost:8001/api/v1/auth/refresh \
  -H 'content-type: application/json' \
  -d "{\"refresh_token\":\"<refresh>\"}"

# 3. 旧 refresh 复用 -> 401 token_revoked
curl -X POST localhost:8001/api/v1/auth/refresh \
  -H 'content-type: application/json' \
  -d "{\"refresh_token\":\"<旧 refresh>\"}"

# 4. logout (Authorization access + refresh body) -> revoked_access=true / revoked_refresh=true
curl -X POST localhost:8001/api/v1/auth/logout \
  -H "Authorization: Bearer <access>" \
  -H 'content-type: application/json' \
  -d "{\"refresh_token\":\"<refresh>\"}"

# 5. logout 后 /me -> 401 token_revoked
curl -H "Authorization: Bearer <已 logout 的 access>" localhost:8001/api/v1/me
```

---

### BE-005 · 微信小程序登录 ✅ DONE

**改动文件**（已实装）
- `apps/api/app/adapters/wechat/__init__.py`、`mp_login.py`（`code2Session` 客户端 + `WechatAuthError` / `WechatAPIError` 二分错误模型 + DI 单例）
- `apps/api/app/services/user_service.py` 加 `find_user_by_wechat_unionid` / `find_user_by_wechat_openid`
- `apps/api/app/services/auth_service.py` 加 `find_or_create_user_by_wechat` + `verify_wechat_mp_login` + `_create_user_with_wechat`
- `apps/api/app/schemas/auth.py` 加 `WechatMpLoginRequest`
- `apps/api/app/api/v1/auth.py` 加 `POST /auth/login/wechat-mp`（限流 + 503/401/502 错误映射）
- `apps/api/app/core/config.py` + `.env(.example)` 加 `WECHAT_MP_APP_ID` / `WECHAT_MP_APP_SECRET` / `WECHAT_CODE2SESSION_URL` / `WECHAT_CODE2SESSION_TIMEOUT_SECONDS`
- `apps/api/tests/test_wechat_login.py`（21 用例：10 个 adapter 单元 [respx] + 11 个路由端到端 [DB + stub client]）
- ⚠️ **不需要新 alembic migration**：`users.wechat_openid` (unique) + `users.wechat_unionid` (索引) 在 INFRA-001 就已建好

**AC**（已验证）
- [x] `POST /auth/login/wechat-mp {code}` → 调 `https://api.weixin.qq.com/sns/jscode2session` → 拿 openid/unionid
- [x] `users.wechat_unionid` 索引就位（INFRA-001 内已建）
- [x] 已绑定 unionid 的用户直接登录；新用户自动注册（含 invite_code 冲突重试）
- [x] **跨小程序场景**：同 unionid 但 openid 变了，命中老用户后 openid 字段自动同步覆盖
- [x] **unionid 回填**：先用 openid 注册的老用户，后续登录拿到 unionid 时补回 user 行
- [x] 错误码二分映射:
   - `40029` (invalid code) / `41008` (missing code) → `WechatAuthError` → 401 `wechat_code_invalid`
   - `-1` / `45011` / `40013` 等 → `WechatAPIError` → 502 `wechat_upstream_error`
   - 网络超时 / HTTP 5xx / 非 JSON → 502
   - 未配置 AppSecret → 503 `wechat_mp_not_configured`
   - 老用户 `status != 1` → 401 `user_disabled`（不可借微信登录绕过封禁）
- [x] **限流**：同 code 1min 内 5 次，第 6 次 429（namespace=`wechat_mp_login`，key=code[:32]）
- [x] **合规护栏**：`session_key` 不进 `Code2SessionResult`、不入库、不打日志
- [x] respx 单测覆盖 happy（含/不含 unionid、空串 unionid）+ 用户类 errcode + 系统类 errcode + 超时 + HTTP 5xx + 非 JSON
- [x] 路由端到端：新用户、老用户(unionid 命中 + openid 跨小程序覆盖)、老用户(openid fallback)、老用户(unionid 回填)、401/502/503/422/429/disabled
- [x] 测试结果：`pytest -q` → **131 passed** with DB / 75 passed + 56 skipped without DB
- [x] 烟测验证 503 / 422 / 200(新用户+unionid) / 200(老用户) / 401(40029) / 502(-1) 全路径

**关键设计点**
- **错误码二分**：用户态 (40029/41008) vs 服务态 (-1/40013/45011/网络/超时)。前者让前端 `wx.login()` 重取 code，后者让前端 retry 或提示稍后再试 — 两个方向的错误恢复 UX 不同，所以路由层要分开 401 vs 502。
- **session_key 合规**：腾讯红线，落库或回传客户端立刻封号；适配器层 dataclass 直接不收这个字段，从源头杜绝。
- **可注入的单例 client**：`get_wechat_mp_client` / `set_wechat_mp_client` / `reset_wechat_mp_client` 三件套，路由测试不用 respx monkey-patch ASGI 栈，直接换 stub class 即可，避免 respx + ASGITransport 的兼容坑。
- **unionid > openid**：unionid 是开放平台跨小程序/公众号身份，openid 仅在单一小程序内稳定。优先按 unionid 找用户，找到后 openid 用本次登录拿到的最新值覆盖（用户在另一关联小程序登录后会换 openid，但 unionid 不变）。
- **fail-close, not fail-open**：微信侧 5xx / 超时一律 502 拒绝登录，绝不"假设成功"放过去，否则等于绕过身份验证。
- **限流 key 选 code 前缀而非用户**：路由层还没有用户身份；wx.login 的 code 微信侧本身只能用一次，但加这层 5/min 防 code 被偷后暴力试。

**Smoke test**

```bash
cd apps/api && source .venv/bin/activate
PYTHONUNBUFFERED=1 uvicorn app.main:app --host 127.0.0.1 --port 8001
# 503 (未配置 AppSecret)
curl -X POST localhost:8001/api/v1/auth/login/wechat-mp \
  -H "Content-Type: application/json" -d '{"code":"081Test1234567890"}'
# 422 (code 太短)
curl -X POST localhost:8001/api/v1/auth/login/wechat-mp \
  -H "Content-Type: application/json" -d '{"code":"x"}'

# 配真实 AppID/AppSecret 后 (或本地 mock 微信接口):
WECHAT_MP_APP_ID=xxx WECHAT_MP_APP_SECRET=yyy \
  uvicorn app.main:app --host 127.0.0.1 --port 8001
# 200 (含 unionid 的小程序)
curl -X POST localhost:8001/api/v1/auth/login/wechat-mp \
  -H "Content-Type: application/json" -d '{"code":"<wx.login 拿到的 code>"}'
```

---

### BE-006 · 邀请码 ✅ DONE

**改动文件**（已实装）
- `apps/api/app/services/invite_service.py`（`register_invite_code_for_user` + `bind_invite` + 7 类 `InviteError` 子异常 + `InviteBindResult` DTO）
- `apps/api/app/api/v1/invite.py`（`POST /invite/bind`，登录态 + 限流 10/min/user，全错误码映射）
- `apps/api/app/api/v1/__init__.py` 注册 `invite.router`
- `apps/api/app/services/auth_service.py` 在 `_create_user_with_phone` / `_create_user_with_wechat` 中**同事务**调 `register_invite_code_for_user`，注册原子地落 `invite_codes` 行
- `apps/api/app/schemas/invite.py`（`InviteBindRequest` + `InviteBindResponse`，`code` `min_length=4`/`max_length=16`，自动 strip + upper 归一）
- `apps/api/tests/test_invite.py`（16 个端到端测试 + 1 个 service 层单测）
- `apps/api/tests/test_refresh.py`：顺手修了一个 flaky 用例（改 JWT 末位字符不稳定，改成中部位置）
- ⚠️ **不需要新 alembic migration**：`invite_codes` 表 + `users.invited_by` 在 INFRA-001 已建好；不引入 `utils/short_id.py`，沿用 BE-002 的 8 字符大写+数字（`62^8 ≈ 2.18e14`，远超 backlog 写的 6 位 base62 ~`56e9`，不必降级）

**AC**（已验证）
- [x] **注册即落码**：phone / wechat 两个注册入口都在同一事务里把 `users.invite_code` 镜像到 `invite_codes` 行（owner_user_id=新用户、`max_usage=NULL` 无限、`is_active=true`、`note='personal'`）
- [x] `POST /invite/bind {code}` 登录态下绑定 referrer，**一次性**（`users.invited_by` 用条件 `WHERE invited_by IS NULL` UPDATE 防双绑）
- [x] **自禁**：`code == own_invite_code`（含大小写归一后撞）→ 400 `invite_self_binding`
- [x] **重复绑** → 400 `invite_already_bound`（两层防御：fast-fail + conditional UPDATE 防并发）
- [x] **不存在** → 404 `invite_code_not_found`
- [x] **运营态控制**：`is_active=false` → 400 `invite_code_inactive`；`expires_at < now` → 400 `invite_code_expired`；`usage_count >= max_usage` → 400 `invite_code_exhausted`
- [x] **运营码（owner_user_id IS NULL）拒绑** → 400 `invite_code_not_personal`（MVP 范围；后续渠道追踪功能再处理）
- [x] **并发安全**：`SELECT ... FOR UPDATE` 锁 `invite_codes` 行 + conditional UPDATE 锁 `users.invited_by`；`usage_count` 在锁内自增不会少计
- [x] **限流**：同 user 1 min 10 次（`namespace=invite_bind`，防暴力扫码）
- [x] **大小写归一**：schema 自动 `strip + upper`，"abcd1234" 与 "ABCD1234" 视为同一码
- [x] **测试结果**：`pytest -q` → **147 passed** with DB / 75 + 72 skipped without DB
- [x] **烟测**：bind happy / 二次绑 / 自禁 / 不存在 / 未登录全路径

**关键设计点**
- **一次性 + 不可改 referrer**：`invited_by` 字段一旦写入禁止覆盖。这是为了防止刷邀请奖励的攻击模式（"先绑 A 拿一份奖励，改绑 B 再拿一份"）。如果将来产品需要"换邀请人"则要走人工申诉，不放进 API。
- **两层并发防御**：service 层先用 `current_user.invited_by is not None` fast-fail（避免去碰 `invite_codes` 表），再用 `WHERE invited_by IS NULL` conditional UPDATE 抓真并发（rowcount=0 → 期间被另一请求写过）。
- **`SELECT ... FOR UPDATE`**：锁 invite_codes 行后再做 `usage_count += 1`，避免两个请求同时通过 max_usage 校验后双双累加导致超额。
- **运营码与个人码分离**：`owner_user_id IS NULL` 的码 MVP 当作"渠道追踪码"语义，不允许作为 referrer 来源（因为没有具体的"谁邀请了我"）。后续 BE-006 之后增 `channel_code` 字段时这块语义会扩展。
- **个人码默认 `max_usage=NULL`**：朋友圈裂变期不限单人传播次数。运营如要限速，UPDATE 单条 `max_usage` 即可，不影响其它用户。
- **两个事务边界**：注册 (`auth_service`) 同事务里写 user + invite_code 行，任一失败一起回滚；绑定 (`invite_service.bind_invite`) 内部 commit，路由层不再 commit（路由 try 捕异常时已经 rollback 过）。
- **schema strip + upper**：用户在小程序里手输 `a` / `A` 容易混；DB 端只存大写，schema 端入库前归一（避免 service 层每次 case-insensitive 查询）。
- **flaky test 修复**：`test_refresh_with_tampered_returns_401_invalid` 之前用 "改 token 最后一位字符" 来制造无效签名，但 base64url 末位的低 bits 可能是 padding，改字符不一定真改签名 bits。改成"改倒数第 8 位"以稳定命中签名段。

**Smoke test**

```bash
cd apps/api && source .venv/bin/activate
PYTHONUNBUFFERED=1 uvicorn app.main:app --host 127.0.0.1 --port 8001

# 1) 注册 referrer + invitee
curl -X POST localhost:8001/api/v1/auth/otp/send -H 'content-type: application/json' \
  -d '{"phone":"+8613900100001"}'
CODE_R=$(redis-cli get 'xgzh:otp:+8613900100001')
RB=$(curl -sS -X POST localhost:8001/api/v1/auth/login/phone \
  -H 'content-type: application/json' \
  -d "{\"phone\":\"+8613900100001\",\"code\":\"$CODE_R\"}")
REFERRER_CODE=$(echo "$RB" | python3 -c 'import json,sys;print(json.load(sys.stdin)["user"]["invite_code"])')

curl -X POST localhost:8001/api/v1/auth/otp/send -H 'content-type: application/json' \
  -d '{"phone":"+8613900100002"}'
CODE_I=$(redis-cli get 'xgzh:otp:+8613900100002')
IB=$(curl -sS -X POST localhost:8001/api/v1/auth/login/phone \
  -H 'content-type: application/json' \
  -d "{\"phone\":\"+8613900100002\",\"code\":\"$CODE_I\"}")
INVITEE_TOKEN=$(echo "$IB" | python3 -c 'import json,sys;print(json.load(sys.stdin)["tokens"]["access_token"])')

# 2) 绑定: 200
curl -X POST localhost:8001/api/v1/invite/bind \
  -H "Authorization: Bearer $INVITEE_TOKEN" \
  -H 'content-type: application/json' \
  -d "{\"code\":\"$REFERRER_CODE\"}"
# {"ok":true,"referrer_user_id":"...","referrer_invite_code":"...","bound_at_usage_count":1}

# 3) 二次绑: 400 invite_already_bound
# 4) 自禁: 400 invite_self_binding (用自己的码)
# 5) 不存在: 404 invite_code_not_found
# 6) 未登录: 401 token_missing
```

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
