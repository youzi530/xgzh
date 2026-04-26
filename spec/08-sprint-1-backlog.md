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
| BE-007 ✅ | backend | IPO 表持久化 + AKShare 调度入库 | 1d | INFRA-001 | P0 |
| BE-008 ✅ | backend | `GET /ipos` 切回数据库 + 筛选 + Redis 缓存 | 0.5d | BE-007, INFRA-002 | P0 |
| BE-009 ✅ | backend | `GET /ipos/{code}` 字段聚合（HKEX/AKShare 多源 merge） | 0.5d | BE-008 | P0 |
| BE-010 ✅ | backend | 用户自选股表 + add/remove API | 0.5d | BE-003, BE-008 | P0 |
| BE-011 ✅ | backend | 推送 token 注册 + 设备表 | 0.5d | BE-003 | P1 |
| FE-001 ✅ | frontend | 登录页（手机号 OTP） + 微信小程序一键登录 | 1d | BE-002, BE-005 | P0 |
| FE-002 ✅ | frontend | Auth Pinia store + uni.request 拦截器 | 0.5d | FE-001 | P0 |
| FE-003 ✅ | frontend | 个人中心 + 设置 + VIP 入口（无支付） | 1d | FE-002 | P0 |
| FE-004 ✅ | frontend | 首页瀑布流 + 今日打新卡片 + 打新日历 | 1.5d | BE-008 | P0 |
| FE-005 ✅ | frontend | 新股详情页（关注按钮 + 招股要点） | 1d | BE-009, BE-010 | P0 |
| FE-006 ✅ | frontend | 自选列表 Tab | 0.5d | BE-010, FE-005 | P0 |
| QA-001 ✅ | qa | API 集成测试套件（pytest + httpx） | 1d | BE-009 | P0 |

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

### BE-007 · IPO 表持久化 + AKShare 调度入库 ✅ DONE

**目标**：把每日 AKShare A 股 IPO 列表 upsert 进 `ipos` 表，给 BE-008 (`GET /ipos`
切回 DB) 准备好数据；HK 仍走 seed，Sprint 2 接 HKEX 后再切。

**改动文件**（已实装）
- `apps/api/pyproject.toml`：加 `apscheduler>=3.10,<4`
- `apps/api/app/core/config.py`：加 `scheduler_enabled` / `ipo_ingest_initial_delay_seconds`
  / `ipo_ingest_cron_hours` / `ipo_ingest_a_limit` / `ipo_ingest_timezone`
- `apps/api/.env(.example)`：加 5 个新配置项
- `apps/api/app/services/ipo_ingest_service.py`（新建）：`upsert_ipos(session, items)` +
  `run_ingest_a_job()` 后台入口（吞异常 + 自管 session + COALESCE 兜底防擦数据）
- `apps/api/app/scheduler/__init__.py`（新建）：`AsyncIOScheduler` 单例 +
  `register_jobs` (启动后 N 秒一次 + cron 08:00/20:00 Asia/Shanghai) + `start_scheduler` /
  `shutdown_scheduler`
- `apps/api/app/main.py`：lifespan 启动 scheduler + finally 优雅关闭
- `apps/api/tests/test_ipo_ingest.py`（新建，10 用例）
- `apps/api/tests/test_jwt.py`：顺手把 BE-004 残留的 last-char tampering flaky 测试改稳

**关键设计决策**
1. **upsert 走 PG `ON CONFLICT (code, market) DO UPDATE` 一条 SQL**，不是 ORM
   row-by-row select-update，AKShare 200 行一次入库 < 100ms。
2. **COALESCE 兜底"新值为 NULL 不擦旧值"**：`industry_l1` / `pe_ratio` / `issue_price` 等
   在 update 分支用 `COALESCE(EXCLUDED.x, ipos.x)`，避免周期任务把人工补录字段刷 NULL；
   `name` / `extra` / `updated_at` 强制覆盖。
3. **`run_ingest_a_job` 永不抛**：fetch / parse / DB 任何异常都 `logger.exception`
   后返回 `{"errors": 1}`，防止 APScheduler 把整个 job 标 failed 后停掉。
4. **`coalesce=True` + `max_instances=1`**：调度堵塞 / 实例重启时多次错过的执行只补跑
   一次，且永远不会两个 ingest 同时跑（撞 PG upsert 倒还行，但浪费 AKShare 配额）。
5. **多副本部署**：K8s 上多副本 web pod 关 `SCHEDULER_ENABLED=false`，单独跑一个
   worker pod 开。这样不需要分布式锁也安全（spec/06 ops 章节后补）。
6. **HK 仍 seed**：akshare 1.18 没干净的 HK IPO API，已留 TODO 指向 spec/04 §4，
   Sprint 2 接 HKEX/Futu 后启用 `run_ingest_hk_job`。

**AC**
- [x] 启动时立即跑一次 ingest（`IPO_INGEST_INITIAL_DELAY_SECONDS=5` 秒后触发；可设 0 关掉）
- [x] 每天 08:00 / 20:00（Asia/Shanghai）跑全量更新（`IPO_INGEST_CRON_HOURS=8,20` 可调）
- [x] upsert 按 `(code, market)` 唯一约束去重，第二次跑同样数据 `inserted=0 updated=200`
- [x] fetch 失败 / DB 失败 / akshare 返回空 都不让进程崩
- [x] HK 暂仍用 seed（`fetch_hk_ipos` 已留 TODO，Sprint 2 接 HKEX）
- [x] lifespan 启动/关闭 scheduler，graceful shutdown 不阻塞 web 关闭（用 `wait=False`）
- [x] 烟测：`run_ingest_a_job()` 真打 AKShare → 200 行入库；二次跑 → 200 行 update，无重复

**测试**
- 共 **10 个**新增用例：
  - `upsert_ipos`: 空列表早 return / 全 INSERT / 二次 UPDATE 不增行 / NULL 不擦旧值
  - `run_ingest_a_job`: happy path / fetch 抛异常吞掉 / 空结果安全
  - `register_jobs`: 默认 2 jobs / `initial_delay=0` 只剩 cron / 重入安全
- DB 测试通过 `monkeypatch` 把 `akshare_client.fetch_a_ipos` 替成固定 fixture，再用
  `patch_session_factory` fixture 把 `get_session_factory` LRU 替成测试库 factory，
  完全不打外网。

**烟测**

```bash
# 启动 web (lifespan 拉起 scheduler)
PYTHONUNBUFFERED=1 IPO_INGEST_INITIAL_DELAY_SECONDS=0 IPO_INGEST_CRON_HOURS=22 \
  uvicorn app.main:app --port 8765
# 期望日志: scheduler.jobs_registered ... scheduler.started

# 一次性跑入库 (走真 AKShare)
python -c "
import asyncio
from app.services import ipo_ingest_service
print(asyncio.run(ipo_ingest_service.run_ingest_a_job()))
"
# → received=200 inserted=200 updated=0
# 再跑一次:
# → received=200 inserted=0 updated=200 (验证 upsert 语义)

psql ... -c "SELECT count(*), count(*) FILTER (WHERE updated_at > created_at) FROM ipos;"
# total=200, updated_after_first=200
```

**遗留 / Sprint 2**
- `run_ingest_hk_job`：等 HKEX/Futu 真源 (spec/04 §4)
- 监控告警：APScheduler `EVENT_JOB_ERROR` 暂只走 logger.exception，未接 Sentry/钉钉
- 多副本协调：当前是"哪个 pod 配 `SCHEDULER_ENABLED=true` 哪个跑"，没用 PG advisory
  lock 做分布式互斥；规模上来后再补

---

### BE-008 · `GET /ipos` 切回数据库 + 筛选 + Redis 缓存 ✅ DONE

**目标**：把 `GET /api/v1/ipos` 从每请求打 AKShare（48s+ 老坑）切到读 `ipos` 表 +
Redis 缓存。HK 仍走 seed（akshare 没干净的 HK API），Sprint 2 接 HKEX 后切回 DB。

**改动文件**（已实装）
- `apps/api/app/schemas/ipo.py`：`IPOListResponse` 加 `page` / `size`（带
  `ge=1, le=100` 校验），把分页元信息一起返回
- `apps/api/app/services/ipo_service.py`：彻底重写
  - `_orm_to_item(row)` ORM → schema 映射，把 `industry_l1` 还原成 schema 的
    `industry`，从 `extra` JSONB 读回 `one_lot_winning_rate`
  - `_list_ipos_db(factory, ...)` 真打 DB：`market` 强制 + `status` / `industry_l1`
    可选筛选；`ORDER BY listing_date DESC NULLS LAST, code ASC` + `LIMIT/OFFSET`
    分页；`SELECT count(*)` 给前端一个 total
  - `list_ipos(*, market, status, industry, page, size)` 入口套
    `@cached(ttl_seconds=600, namespace="ipos:list")`，HK 走 seed 内存筛选+分页，
    A 走 DB，US 占位返回空；返回 dict（不是 Pydantic）让 cache JSON 序列化干净
  - `get_ipo(code)` 顺手切 DB（A/US 路径走 `ipos` 表，HK 仍 seed）
- `apps/api/app/api/v1/ipos.py`：加 `status` / `industry` / `page` / `size` query
  参数 + `IPOListResponse.model_validate(payload)` 把 service 层 dict 还原为 schema
- `apps/api/tests/test_ipos_list.py`（新建，16 用例）

**关键设计决策**
1. **service 边界返回 dict 而非 Pydantic 模型**：`@cached` 装饰器内用
   `json.dumps(result, default=str)` 写缓存，Pydantic v2 实例不能直接 dump
   （会变成奇怪的 stringify），让 service 在 dict 边界，路由层 `model_validate`
   重构成 schema，干净且 cache 可读。
2. **cache key 含全部参数**：装饰器 `_hash_args` 自动把 `(market, status, industry,
   page, size)` 五元组 hash 进 key，所以"换一个 size 就换一个 key"，不会串扰。
   命名空间 `ipos:list`，全局再加 `xgzh:` 前缀，最终 key 例：
   `xgzh:cache:ipos:list:list_ipos:67dea2d93813bb98`。
3. **TTL=600s（10min）**：BE-007 cron 每 12h 抓一次，缓存最多 stale 10min，
   用户感知不到；高 QPS 时有效降 DB / akshare 压力。
4. **排序 `listing_date DESC NULLS LAST, code ASC`**：已上市的按时间倒排（最新
   排前），没 `listing_date` 的 (upcoming/withdrawn) 排到最末；同日上市按 code
   稳定排序，避免分页跳页时顺序漂移。
5. **`IPOListResponse.total` = 全部命中条数（不分页前）**：前端可以正确显示"共 N
   条 / 第 X 页"，不会因 limit 截断而误显示 total。
6. **HK 仍 seed**：内存里做 status / industry 筛选 + 分页，跟 DB 路径行为一致。

**AC**
- [x] A 股列表查询走 DB（200 行 SQL ~10ms，配上 200 行 limit），不再每次打 akshare
- [x] 10 分钟 Redis 缓存（key 含 5 元组参数 hash）
- [x] 支持分页 `page` (1-based) / `size` (1-100)，默认 1 / 20
- [x] 缓存命中时 P95 < 50ms（烟测实测 ~12ms）
- [x] 单测覆盖：HK seed / A DB / status / industry / 分页连贯 / 排序 NULL last /
      US 占位 / 422 / 缓存命中 / 缓存不同参数不串
- [x] 烟测：`xgzh:cache:ipos:list:list_ipos:*` 在 Redis 里能看见，TTL 接近 600s

**测试**
- 共 **16 个**新增用例：
  - HK seed (no DB): 默认全返回 / status 筛选 / industry 筛选 / 分页
  - 入参校验 (no DB): `size>100` → 422 / `page=0` → 422
  - US 占位: `market=US` → 200 + items=[]
  - A 股 (DB): 默认排序 NULL last / status / industry / 分页 1-2-3 不重叠
  - 缓存: 同参数二次调用不打 DB / size 改了重新打 DB
  - `/ipos/{code}`: A 命中 / HK 命中 seed / 不存在 → 404 (HK + A 两路径)
- DB 测试通过 `patch_session_factory` fixture 同时 patch
  `app.db.get_session_factory` + `app.services.ipo_service.get_session_factory` +
  `app.services.ipo_ingest_service.get_session_factory`，确保 service / 灌种子
  / lifespan scheduler 全部用测试库 factory。

**烟测**

```bash
# 1. 列表 + 排序
curl 'http://localhost:8000/api/v1/ipos?market=A&size=3'
# → 200, items 按 listing_date DESC 排序

# 2. 筛选 + 分页
curl 'http://localhost:8000/api/v1/ipos?market=A&status=listed&page=2&size=3'
# → 200, total=200, page=2, items=[3 条]

# 3. 详情
curl http://localhost:8000/api/v1/ipos/688820.SH
# → 200, 盛合晶微

# 4. US 占位
curl 'http://localhost:8000/api/v1/ipos?market=US'
# → {"items":[],"total":0,"market":"US","page":1,"size":20}

# 5. 入参校验
curl -o /dev/null -w '%{http_code}\n' 'http://localhost:8000/api/v1/ipos?market=A&size=200'
# → 422

# 6. 缓存可观察
redis-cli KEYS 'xgzh:cache:ipos:list:*'
# → xgzh:cache:ipos:list:list_ipos:67dea2d9...
redis-cli TTL 'xgzh:cache:ipos:list:list_ipos:67dea2d9...'
# → 接近 600s (装饰器 TTL=600)
```

**遗留 / 后续**
- ~~缓存失效：BE-007 ingest 完成后没主动清 `ipos:list` 缓存，最差 10min stale~~ → ✅ Sprint 1.5 收尾包 `cache.invalidate_namespace("ipos:list", "ipos:detail")` 已接入 `run_ingest_a_job` 末尾，SCAN + UNLINK 实现，详见本文档 §Sprint 1.5
- 排序选择：当前固定 `listing_date DESC NULLS LAST, code ASC`，没暴露 `sort` 参数。
  BE-009 详情或 FE-004 首页有需求时再加。
- HK 切 DB：等 Sprint 2 HKEX adapter 落地。

---

### BE-009 · `GET /ipos/{code}` 字段聚合 ✅ DONE

**改动文件**
- `apps/api/app/schemas/ipo.py`：新增 `IPODetail`（继承 `IPOItem` + `prospectus_url` / `sponsors` / `underwriters` / `highlights` / `risks` / `financial_summary`）
- `apps/api/app/services/ipo_service.py`：
  - `_orm_to_detail(row)`：把 ORM `IPO` 行 + `extra` JSONB 提到 `IPODetail`
  - `get_ipo_detail(code)`：A/US 走 DB、HK 走 seed；返回 `dict`（缓存友好）
  - `@cached(ttl=1800s, namespace="ipos:detail")`，`skip_if_none=True` 防 404 穿透
- `apps/api/app/api/v1/ipos.py`：`GET /ipos/{code}` 切到 `response_model=IPODetail`，404 返回 `{"detail": {"code": "ipo_not_found", "message": ...}}`
- `apps/api/tests/test_ipo_detail.py`：8 条新用例
- `apps/api/tests/test_me.py`：顺手把 JWT 篡改测试改成动 8th-to-last 字符（与 `test_jwt.py` 同款），稳定破签

**关键设计决策**

1. **保留 `get_ipo` 给内部用，路由统一走 `get_ipo_detail`**
   - `agent_service.diagnose_stream` 只需 `IPOItem` 级别 prompt context，不需要 sponsors/highlights，继续用 `get_ipo`，不被 30min 详情缓存干扰
   - 路由 `GET /ipos/{code}` 改用 `get_ipo_detail`，享受详情缓存
2. **`extra` JSONB 不直接 expose**
   - 详情 schema 显式挑出已结构化的 `highlights` / `risks` / `financial_summary`；其它 `extra.*` 字段（如 `internal_*`、`one_lot_winning_rate` 等）不漏给客户端，schema 演进可控
   - 类型不对（如 `extra.highlights` 是 str 而非 list）时优雅降级为空 list，绝不 5xx
3. **`@cached` namespace 分层 + TTL 阶梯**
   - 列表 `ipos:list` TTL 600s（10min）— 用户高频访问
   - 详情 `ipos:detail` TTL 1800s（30min）— 字段变化慢（招股书/保荐人 12h+ 不变），给得更长
   - 不存在的 code 不进缓存（`skip_if_none=True`），运营/cron 后续 ingest 入库后立即可见
4. **404 错误码标准化**：`{"detail": {"code": "ipo_not_found", "message": ...}}`，与登录/邀请码错误体保持同构（前端可机器解析 `code`）
5. **占位字段策略**：`highlights` / `risks` / `financial_summary` 当前从 `extra` JSONB 读，BE-018 招股书 RAG 落地后由 ingest pipeline 写入；schema 已先暴露，前端可以并行接 UI

**AC**
- [x] `GET /api/v1/ipos/{code}` 返回 `IPODetail`（含 `sponsors` / `underwriters` / `prospectus_url` / `highlights` / `risks` / `financial_summary`）
- [x] A/US 走 DB，HK 走 seed
- [x] `extra` JSONB 中 `highlights` / `risks` / `financial_summary` 自动提取到顶层字段
- [x] 不存在返回 404 + `code: "ipo_not_found"`
- [x] 缓存 30 分钟（Redis 验证 TTL ≈ 1800s）
- [x] 缓存 miss → hit 时延 13ms → 2ms

**测试 (`tests/test_ipo_detail.py`，8 条)**
- HK seed hit / 404
- A DB 完整 merge（sponsors + extra.highlights + financial_summary）
- A DB 无 extra 时返回安全默认（空 list / None）
- A DB extra 类型损坏（str 而非 list）不 500
- A DB 404 + 错误码
- 缓存命中（同 code 第二次不 hydrate ORM）
- 404 不缓存（先 404，ingest 后再请求能拿到）

**烟测**
```bash
# 1) HK seed 详情
curl -s http://127.0.0.1:8001/api/v1/ipos/02015.HK | jq

# 2) A DB 详情（已 ingest 过的 code，extra 没填 → highlights/risks 为空 list）
curl -s 'http://127.0.0.1:8001/api/v1/ipos?market=A&size=1' | jq '.items[0].code'
curl -s http://127.0.0.1:8001/api/v1/ipos/<code> | jq

# 3) 模拟运营写 sponsors / extra.highlights
psql -U xgzh -d xgzh -c "UPDATE ipos SET sponsors='[\"中金公司\",\"华泰\"]'::jsonb,
  extra=jsonb_set(coalesce(extra,'{}'::jsonb),'{highlights}','[\"亮点A\",\"亮点B\"]'::jsonb)
  WHERE code='<code>';"

# 4) 清详情缓存（不然拿到旧的）
redis-cli --scan --pattern 'xgzh:cache:ipos:detail:*' | xargs -I{} redis-cli DEL {}

# 5) 再请求 → 看到新字段
curl -s http://127.0.0.1:8001/api/v1/ipos/<code> | jq

# 6) 不存在 → 404
curl -sw "\nHTTP=%{http_code}\n" http://127.0.0.1:8001/api/v1/ipos/000999.SZ
```

**遗留 / 待办**
- BE-018 招股书 RAG 落地后，把 `highlights` / `risks` / `financial_summary` 改为 ingest pipeline 自动写入，而不是运营手动 update（Sprint 2 §BE-S2-004 / BE-S2-005 主战场）
- `financial_summary` 后续接入 AKShare `stock_financial_abstract_em` 等接口
- ~~详情缓存应在 ingest 全表 upsert 后失效~~ → ✅ Sprint 1.5 收尾包 `cache.invalidate_namespace` 已统一处理列表 + 详情两个 namespace（详见本文档 §Sprint 1.5）

---

### BE-010 · 用户自选股 + API ✅ DONE

**改动文件**
- `apps/api/app/schemas/favorite.py`：`FavoriteAddRequest` / `FavoriteAddResponse` / `FavoriteRemoveResponse` / `FavoriteItem` / `FavoriteListResponse`
- `apps/api/app/services/favorite_service.py`：
  - `_parse_code(raw) -> (code_upper, market)`：白名单后缀（`.HK` / `.SH` / `.SZ` / `.BJ` / `.US`），不合法抛 `FavoriteCodeInvalidError`
  - `add_favorite`：PG `INSERT ... ON CONFLICT (user_id, ipo_code, market) DO UPDATE` 一条 SQL；`RETURNING (xmax = 0)` 区分 INSERT/UPDATE 路径，省一次 SELECT
  - `remove_favorite`：`DELETE` 后看 `rowcount`，0 行也返 200，幂等
  - `list_favorites`：`user_favorites` LEFT JOIN `ipos` 拿最新行情；按 `created_at DESC, ipo_code ASC` 排
- `apps/api/app/api/v1/favorites.py`：3 个路由，全部 `Depends(get_current_user)`
- `apps/api/app/api/v1/__init__.py`：注册 `favorites.router`
- `apps/api/tests/test_favorites.py`：15 条新用例

**关键设计决策**

1. **`code` 由前端带市场后缀** — 客户端只持一份 `code` 标识就能在"列表/详情/收藏"三处共用，不需要额外维护 `(code, market)` 对；后端用白名单后缀反推 market（`.HK` → HK，`.SH/.SZ/.BJ` → A，`.US` → US），脏数据 400 直接拒，不进表。
2. **幂等 ON CONFLICT** — `POST /favorites` 重复调用只会 `DO UPDATE SET notify_on_subscribe=...`，不会撞 `(user_id, ipo_code, market)` 主键约束；同时支持用户重新收藏时切换"打新提醒"开关。
3. **`RETURNING (xmax = 0)` 区分 INSERT vs UPDATE** — PG 老 trick：`xmax=0` 表示该行刚被本事务 INSERT，非 0 表示走了 `ON CONFLICT DO UPDATE`；比"再发一条 SELECT 判已存在"省一次 round-trip。
4. **删除返 200 不返 404** — `DELETE /favorites/{code}` 即使本来就没收藏也 200 + `removed=False`，前端不需要 try/catch；这跟"幂等删除"的 RESTful 实践一致。
5. **LEFT JOIN ipos 而非 INNER JOIN** — 用户收藏的 HK seed code 当前不在 `ipos` 表（HKEX adapter 排在 Sprint 2），LEFT JOIN 让自选页仍能渲染"占位卡片"，不会因为 ingest 还没跑就让用户看到空 list。
6. **`one_lot_winning_rate` 从 `extra` JSONB 提到顶层** — 与 BE-009 `IPODetail` 同样的策略，`FavoriteItem` 不暴露 `extra`，schema 演进可控。
7. **不分页** — MVP 假设单用户自选 < 100 支；列表里没分页参数。如果后续单用户量大，加 `limit/offset` 即可，schema 保留 `total` 字段已为分页做好准备。
8. **rate limit 暂不加** — `add/remove` 都是幂等 + 单用户作用域，不会造成 DB 雪崩；后续如果有滥用迹象再加 60/min/user 即可。

**AC**
- [x] `POST /favorites {code}` 添加；重复返回 200 + `created=False` 不报错；可切换 `notify_on_subscribe`
- [x] `DELETE /favorites/{code}` 移除；不存在也返 200 + `removed=False`
- [x] `GET /favorites` 返回用户全部自选 IPO（LEFT JOIN ipos 带 name/listing_date/status 等行情字段）
- [x] 必须登录态（401 `token_missing` if no token）
- [x] `code` 不带后缀 / 后缀未知 → 400 `favorite_code_invalid`
- [x] 用户隔离：A 用户的自选 B 用户看不到

**测试 (`tests/test_favorites.py`，15 条)**
- 鉴权：3 个路由分别 401
- POST 路径：首次 created=True / 重复 created=False / toggle notify / 大小写归一 / HK seed 也可收藏 / 无后缀 400 / 未知后缀 400
- DELETE 路径：删除成功 / 重复删除幂等 / 不合法 code 400
- GET 路径：空 / 混合 A+HK 倒序 / 用户隔离

**烟测**
```bash
# 1) 拿 token
PHONE=13800139999
curl -X POST localhost:8000/api/v1/auth/otp/send -d '{"phone":"'$PHONE'"}'
CODE=$(redis-cli get "xgzh:otp:+86$PHONE")
TOKEN=$(curl -X POST localhost:8000/api/v1/auth/login/phone \
  -d "{\"phone\":\"$PHONE\",\"code\":\"$CODE\"}" | jq -r .tokens.access_token)
H="Authorization: Bearer $TOKEN"

# 2) 添加 (DB-backed A 股)
curl -X POST localhost:8000/api/v1/favorites -H "$H" -d '{"code":"920156.SH"}'
# {"ok":true,"code":"920156.SH","market":"A","created":true,...}

# 3) 重复添加 + 切 notify off → created=False
curl -X POST localhost:8000/api/v1/favorites -H "$H" \
  -d '{"code":"920156.SH","notify_on_subscribe":false}'

# 4) HK seed code (ipos 表无) 也可收
curl -X POST localhost:8000/api/v1/favorites -H "$H" -d '{"code":"02015.HK"}'

# 5) GET 混合列表 (HK 字段 None, A 股 LEFT JOIN 出 name/listing_date)
curl -H "$H" localhost:8000/api/v1/favorites | jq

# 6) DELETE 幂等
curl -X DELETE -H "$H" localhost:8000/api/v1/favorites/920156.SH  # removed=true
curl -X DELETE -H "$H" localhost:8000/api/v1/favorites/920156.SH  # removed=false

# 7) 无后缀 → 400
curl -X POST localhost:8000/api/v1/favorites -H "$H" -d '{"code":"BABA"}'
# {"detail":{"code":"favorite_code_invalid",...}}
```

**遗留 / 待办**
- BE-011 推送 token 落地后，cron 在 IPO `status` 进入 `subscribing` 时按 `notify_on_subscribe=true` 推单
- 单用户自选 > 100 时考虑加 `(user_id, created_at)` 复合索引 + 分页参数
- 自选数量限制（MVP 不做）：免费用户 50 支 / VIP 无限，留给 BE-014 VIP 配额校验

---

### BE-011 · 推送 token 注册（P1） ✅ DONE

**目标**：把客户端拿到的 APNs / FCM / 微信小程序订阅 token 收回服务端，养出 Sprint 4 真正发推送时的"投递候选名单"。本 Sprint 不发推，只做注册 / 注销 contract。

**改动文件**（已实装）
- `apps/api/app/schemas/push.py`：`PushPlatform` Literal (`ios|android|wxmp|h5`) + 三个 Pydantic 模型；响应**不回显 ``token``**
- `apps/api/app/services/push_service.py`：`register_token`（PG `ON CONFLICT DO UPDATE` + `RETURNING (xmax = 0)` 区分新增/覆盖）+ `unregister_token`（单条 `DELETE`，幂等）+ `list_user_tokens`（Sprint 4 推送实施时调）
- `apps/api/app/api/v1/push.py`：`POST /push/tokens` + `DELETE /push/tokens?platform=&device_id=`，均挂 `Depends(get_current_user)`
- `apps/api/app/api/v1/__init__.py`：注册 `push.router`
- `apps/api/tests/test_push_token.py`：12 条端到端用例

**AC**
- [x] `POST /push/tokens {platform, token, device_id}` 落 `push_tokens` 表
- [x] 同 `user + platform + device_id` 唯一；复发 = 覆盖 `token` + 重新激活 `is_active`，不新增行
- [x] **响应里不回显 token**（敏感凭据保护，前端本来就持有）
- [x] `DELETE /push/tokens?platform=&device_id=` 幂等（不存在也返 200 + `removed=false`）
- [x] 用户隔离：B 用户无法删 A 用户的 device 记录
- [x] `platform` 非白名单 → 422；`device_id` 缺失 → 422；`token` < 8 字符 → 422
- [x] 不实际推送（Sprint 4 接 APNs / FCM / wxmp 时再实施）

**关键设计决策**
1. **device_id 强制必填非空**（API schema 层）：PG 中 `UNIQUE (user_id, platform, device_id)` 在 `device_id IS NULL` 时**不去重**（NULL 互不相等是 SQL 标准的老坑），如果允许 NULL 客户端反复注册会无限堆行。强制非空让 `ON CONFLICT` 行为可预期，成本只是前端必须给一个稳定的设备标识（小程序用 openid hash、H5 用 cookie hash 即可）。
2. **响应不 echo token**：APNs / FCM token 一旦泄露第三方可代发垃圾消息，安全大于便利。客户端本身就持有 token，无需后端再回传。
3. **复合 DELETE 条件 `(user_id, platform, device_id)`**：即便 `device_id` 由客户端控制，越权也只能影响"绑到自己 user_id 的设备"，杜绝跨用户污染；同时不暴露 `push_tokens.id` 主键，让删除接口语义全面对齐"按设备唯一性"。
4. **覆盖时强制 `is_active = true`**：将来如果运营加了"禁用 token"功能把 `is_active` 置 false，用户重新注册同 device 应自动重新激活，避免"明明 APP 已经在前台却收不到推送"的诡异 case。
5. **`xmax = 0` trick 沿用 BE-010**：单 SQL 同时拿到 `id` + 是否新建 + `created_at` / `updated_at`，省一次 SELECT。
6. **id 序列空洞是预期**：`BIGSERIAL` 在 `ON CONFLICT DO UPDATE` 命中时也会消耗一个 sequence 值（PG 行为），所以 `id=1, 3, 5...` 不连续是正常的，不影响功能。

**测试结果**
```
有 DB:  uv run pytest -q  →  208 passed (新增 12)
无 DB:  uv run pytest -q  →   89 passed, 119 skipped
```

**烟测命令**
```bash
# 0. 起服务
cd apps/api && PYTHONUNBUFFERED=1 uv run uvicorn app.main:app --host 127.0.0.1 --port 8011

# 1. 走完登录拿 ACCESS（略，见 BE-002）

# 2. 注册 iOS token (created=true, 响应里没 token 字段)
TOK1=$(printf "a%.0s" {1..64})
curl -s -X POST localhost:8011/api/v1/push/tokens \
  -H "Authorization: Bearer $ACCESS" -H 'content-type: application/json' \
  -d "{\"platform\":\"ios\",\"token\":\"$TOK1\",\"device_id\":\"iphone-15\"}"

# 3. 同 device 复发新 token (created=false, id 不变, DB 里 token 被覆盖)
TOK2=$(printf "b%.0s" {1..64})
curl -s -X POST localhost:8011/api/v1/push/tokens \
  -H "Authorization: Bearer $ACCESS" -H 'content-type: application/json' \
  -d "{\"platform\":\"ios\",\"token\":\"$TOK2\",\"device_id\":\"iphone-15\"}"

# 4. 注销 iphone-15 (removed=true)
curl -s -X DELETE "localhost:8011/api/v1/push/tokens?platform=ios&device_id=iphone-15" \
  -H "Authorization: Bearer $ACCESS"

# 5. 重复注销 (removed=false, 仍 200)
curl -s -X DELETE "localhost:8011/api/v1/push/tokens?platform=ios&device_id=iphone-15" \
  -H "Authorization: Bearer $ACCESS"
```

**遗留 / 后续**
- Sprint 4 接 APNs / FCM / wxmp 订阅消息时调 `push_service.list_user_tokens(user_id)` 取活跃 token 群发
- 未来可加"运营禁用 token"管理后台接口，把 `is_active=false`；本 Sprint 字段留好但不暴露
- 未来可考虑把无效 token（推送 401 / Unregistered 反馈）自动清理，避免污染推送候选名单

---

### FE-001 · 登录页（手机 OTP + 微信一键） ✅ DONE

**目标**：在 UniApp 端把 BE-001/002/005 三个鉴权接口落地成一个可用登录页，跑通"手机号 → OTP → JWT 入 storage"全链路；小程序端追加微信一键。

**改动文件**（已实装）
- `apps/mp/api/auth.ts`：类型 + `sendOtp` / `loginPhone` / `loginWechatMp` + `parseAuthError`（按 `detail.code` 拆错误）
- `apps/mp/utils/auth-storage.ts`：access/refresh/user + 各自过期时间戳的轻量 storage helper；含 `isAccessTokenFresh`（预留 60s 安全边际）/ `isRefreshTokenFresh` / `isLoggedIn` / `snapshot`，给 FE-002 Pinia store 接管
- `apps/mp/pages/auth/login.vue`：双 Tab（手机号 / 微信一键）+ 60s 倒计时 + 协议勾选 + 合规 footer + 错误码分支 toast
- `apps/mp/pages.json`：注册 `pages/auth/login`
- `apps/mp/pages/index/index.vue`：右上角 hero 区域加"登录 / 注册"胶囊（已登录显示昵称首字头像，点击占位提示 FE-003 个人中心）

**AC**
- [x] H5 / 小程序双端：手机号 + 验证码登录
- [x] 小程序额外提供微信一键登录按钮（`#ifdef MP-WEIXIN` 条件编译）
- [x] 验证码 60 秒倒计时（前端镜像 + 429 兜底拉起）
- [x] 必含合规 footer：`登录即同意《用户协议》《隐私政策》《免责声明》`
- [x] 协议未勾选时所有提交按钮不可达 + toast 提示
- [x] 登录成功 `uni.reLaunch` 回首页（清返回栈，避免后退看到登录态残留）
- [x] 后端错误码（`otp_invalid` / `otp_expired` / `otp_send_rate_limited` / `otp_verify_rate_limited` / `wechat_code_invalid` / `wechat_mp_disabled` / `wechat_upstream_error`）逐个映射到差异化 UX

**关键设计决策**
1. **TS 字段名 1:1 对齐后端**（`access_token` 不写成驼峰 `accessToken`）：spec/08 的 BE-002 schema 注释里就明确"字段名对齐 OAuth2，前端不再翻译"，前后端共用一套术语，减少双向维护成本。
2. **微信 Tab 仅 mp-weixin 条件编译**：H5 / App 拿不到 `wx.login` 一次性 code，强行显示按钮只会让用户点了报错；通过 `#ifdef MP-WEIXIN` 在编译期就剪掉 Tab + 整个 form。
3. **token 存 storage 而非 Pinia state**：FE-001 不引入 Pinia 持久化插件，先用 `uni.setStorageSync` 把 access/refresh/user 拆 5 个 key 存好；FE-002 Pinia store 包一层 reactive layer 即可，迁移成本接近零。
4. **拆 5 个 key 而非 1 个 JSON**：FE-002 拦截器每个请求都要读 `access_token`，避免 `JSON.parse` 整个对象；过期时间单独存方便 silent refresh 判断。
5. **过期判断含 60s 安全边际**：`isAccessTokenFresh` 提前 60s 视为过期，防"刚好压在过期边沿的请求在路上时 token 失效"。
6. **协议勾选 = 一票否决**：未勾时所有"登录"按钮的 disabled 态 + toast 提示，杜绝"用户没看协议就登录"的合规风险（spec/06 §法律隔离）。
7. **错误码差异化 UX**：
   - `otp_send_rate_limited` → 倒计时强拉 60s（前端时钟漂移兜底，避免用户狂点）
   - `otp_invalid` → 清空验证码输入框，让用户直接重输
   - `otp_expired` → 清验证码 + 重置倒计时，让用户重新发
   - `wechat_mp_disabled` → 自动切回手机号 Tab，不卡死用户
8. **错误解析层 `parseAuthError`**：把后端 `HTTPException(detail={"code","message"})` 解析成 `{code, message}` 元组，UI 业务分支只读 `code`，message 兜底显示；APIError 退化形态（字符串 detail）也覆盖。
9. **首页登录入口**：未登录显示"登录 / 注册"胶囊，已登录显示昵称首字头像（昵称为空走 `invite_code` 首字符兜底）；点击头像目前 toast "FE-003 建设中"占位，不阻塞 FE-001 联调。

**验收方法**（开发者本地手测，因前端工程 `@dcloudio/*` pinned 版本被 npm yank，需先升 deps）

```bash
# 0. 起后端 (含 mock SMS, OTP 会打到日志)
cd apps/api && uv run uvicorn app.main:app --port 8000

# 1. 起前端 (H5 模式最快)
cd apps/mp
# ⚠️ pnpm install 当前会失败 (deps yank), 暂时手动用微信开发者工具直接打开 ./
pnpm dev:h5  # 或者 pnpm dev:mp-weixin

# 2. 浏览器打开 http://localhost:5173 → 点右上角 "登录 / 注册"
# 3. 输入手机号, 点 "获取验证码"
#    - 后端日志看 [MOCK SMS] code=XXXXXX, 复制
#    - 前端按钮显示 "60s 后重发"
# 4. 勾选协议, 输入验证码, 点 "登录 / 注册"
#    - 成功 toast "欢迎加入新股智汇" / "登录成功", 600ms 后 reLaunch 首页
#    - 首页右上角 hero 变为"昵称首字"头像
# 5. 错误路径手测:
#    - 错误验证码 → toast "验证码错误" + 自动清输入框
#    - 60s 内重复获取 → toast "60 秒内只能获取一次验证码"
#    - 不勾协议直接点登录 → toast "请先勾选并同意协议"
# 6. 微信小程序端 (mp-weixin):
#    - 切到"微信一键登录" Tab → 点按钮 → 调 uni.login 拿 code → 后端 401/502/503 路径全打通
```

**遗留 / 后续**
- **`@dcloudio/*` 版本统一升级**：当前 package.json 里 pin 的 `3.0.0-4060920241225001` 已被 npm yank, `pnpm install` 会失败；建议起独立小 PR 升到 vue3 alpha 通道（`3.0.0-alpha-5000820260420001` 或更新），不在 FE-001 scope 内
- FE-002（Pinia store + 拦截器）：把 `auth-storage.ts` 升级成响应式 store + 全局请求拦截器自动注入 `Authorization: Bearer` + 401 自动 silent refresh
- FE-003（个人中心）：替换首页头像点击的占位 toast
- 协议勾选目前是 modal 占位文本；正式上架前需要把 spec/06 的「用户协议 / 隐私政策 / 免责声明」三份正式文本落到 `apps/mp/static/legal/*.html` 走内嵌 webview 显示
- 微信小程序 `wx.getUserProfile` 头像/昵称同步：spec/01 提到的"获取用户信息"按钮在 FE-001 不做，待 FE-003 个人中心统一处理

---

### FE-002 · Auth Pinia store + 拦截器 ✅ DONE

**目标**：把 FE-001 的 storage helper 升级成响应式 Pinia store + 全局请求拦截器，让业务页面只调四个动词（`setSession` / `clearSession` / `refresh` / `logout`），其余靠 store + 拦截器自动协作。

**改动文件**（已实装）
- `apps/mp/stores/auth.ts`：Pinia store；hydrate from storage；并发去重的 silent refresh；`setSession` / `setTokens` / `clearSession` / `refresh` / `logout` 五个 action；`accessToken` / `refreshToken` / `user` / `loggedIn` / `isAccessFresh` / `isRefreshFresh` 响应式状态
- `apps/mp/utils/request.ts`：自动注入 `Authorization: Bearer`；401 `token_expired` → silent refresh + 重试一次；其它 401 → `clearSession` + 跳登录；`skipAuth` 选项给鉴权接口本身用；防抖跳登录避免并发触发多次
- `apps/mp/api/auth.ts`：补 `refreshToken` (BE-004) + `logout` (BE-004) + 类型；`sendOtp` / `loginPhone` / `loginWechatMp` / `refreshToken` 全部 `skipAuth: true`
- `apps/mp/utils/auth-storage.ts`：补 `saveTokens(TokenPair)`，给 refresh rotation 落 storage 用
- `apps/mp/pages/auth/login.vue`：`saveAuth(resp)` 改为 `auth.setSession(resp)`
- `apps/mp/pages/index/index.vue`：`getStoredUser` + `isLoggedIn` 手动调 → `storeToRefs(authStore)` 响应式订阅，删 `onShow` 里的 `refreshAuthState`

**AC**
- [x] `useAuthStore()` 暴露 `user`, `accessToken`, `loggedIn`, `setSession()`, `logout()`, `refresh()`, `clearSession()`
- [x] `uni.storage` 持久化 token（双写：store state + storage）
- [x] 401 时拦截器尝试一次 silent refresh，失败再跳登录页
- [x] 多个并发请求同时 401 仅触发一次 refresh（store 单 inflight Promise）
- [x] 防止从登录页内部发出的 401 触发跳转死循环
- [x] 鉴权接口 (`sendOtp` / `loginPhone` / `loginWechatMp` / `refreshToken`) `skipAuth: true`，不带 access 也不被拦截器误重定向

**关键设计决策**
1. **silent refresh 并发去重在 store 而非拦截器**：
   - 同时 5 个请求 401，store 维护一个 `inflightRefresh` Promise，所有请求 await 同一个 Promise，避免并发 refresh 拉黑 5 次同一个 refresh_token（BE-004 rotation 是一次性的，第 2 次以后会拿到 `token_revoked`，把刚刚成功登录的用户踢下线）
   - 拦截器只负责"什么时候调 refresh / 什么时候放弃跳登录"，并发去重职责单一在 store
2. **storage 是 source of truth, store 是 hot path**：
   - `setSession` / `clearSession` / `setTokens` 都"双写"回 storage，关闭 APP 后再开仍登录
   - 拦截器不引 store 读 token，直接 `readAccessTokenSync()` 读 storage：避免 hydrate race + 模块循环依赖（`request → store → api/auth → request`）
   - store 做 silent refresh 时才需要 `useAuthStore()`，那时 Pinia 已挂载
3. **`token_expired` 是预期路径，其它 401 是登出路径**：
   - `token_expired` → silent refresh + 重试一次（`_isRetry=true` 标志防无限重试）
   - `token_invalid` / `token_revoked` / `token_missing` / `user_not_found` / `user_disabled` → refresh 也救不回来，直接 `clearSession` + 跳登录
   - 后端 BE-003 deps 已经把这 5 种 reason 区分清楚了，前端拦截器一一对应即可
4. **`skipAuth` 是关键开关**：
   - 鉴权接口本身（`/auth/otp/send`、`/auth/login/*`、`/auth/refresh`）必须 `skipAuth: true`，否则 refresh 接口在 access 也过期时会触发自己的 silent refresh → 死循环
   - 完全公开接口（`/healthz` 等）也 `skipAuth: true`，匿名访问不带过期 token 给后端添乱
   - `skipAuth: true` 接口的 401 是业务错（如 `otp_invalid`），不触发跳登录，直接抛给业务层
5. **跳登录用 `navigateTo` 而非 `reLaunch`**：保留页面栈，让用户登完按返回能回到原页面（如详情页 → 触发 401 → 登录 → 返回详情页）。代价是栈可能加深，但小程序栈上限 10 层，业务正常使用够用
6. **跳登录防抖 + 登录页豁免**：
   - `_redirectingToLogin` flag 防止多个并发 401 都触发 navigateTo（避免栈被一连串登录页填满）
   - 通过 `getCurrentPages()` 判断当前已经在登录页就不跳了，防止登录页内部的 401 死循环（理论上 skipAuth 已经避免了，但双保险）
7. **`logout` 后端失败也 `clearSession`**：
   - 用户视角已经"登出"了，不能因 Redis 短暂故障阻塞；最坏 case jti 多保留 30min 直至自然过期，不是安全灾难
   - try/finally 模式：API 调用包在 try 里，finally 强制 `clearSession`
8. **不引入 `pinia-plugin-persistedstate`**：
   - 该插件 hydrate 时机依赖 Vue mount 周期，可能晚于业务请求；自管 storage hydrate 在 store setup 同步执行，更早可靠
   - 也省一个依赖 + 一个潜在版本兼容问题

**典型时序图**

```
请求 → readAccessTokenSync() 读 storage → 注入 Authorization → 后端
                                              ↓ 401 token_expired
                                  store.refresh() (并发去重)
                                              ↓ 成功
                                  setTokens (state + storage)
                                              ↓
                                  重试原请求 (_isRetry=true)
                                              ↓ 200
                                  resolve 业务

                                              ↓ 401 其它
                                  clearSession + navigateTo /pages/auth/login
                                              ↓
                                  reject APIError
```

**验收方法**

```bash
# 前端 (HBuilderX 或修复 deps 后 pnpm dev:h5):
# 1. 登录后 30min (access TTL) 内: 任何业务请求都正常返回, 不发 refresh
# 2. 把后端 .env 的 JWT_ACCESS_TTL_MIN 改成 1, 重启 → 1 分钟后任何请求会
#    自动 silent refresh 然后重试; F12 Network 看到 /auth/refresh 200 接着原请求 200
# 3. 把 storage 里 access_token 手动改成乱码 → 下个请求 401 token_invalid
#    → 自动跳登录页 (clearSession 已生效, 首页 hero 又显示"登录/注册")
# 4. 同时点 5 个会发请求的按钮 → /auth/refresh 仅请求 1 次, 不会出现 token_revoked
```

**遗留 / 后续**
- FE-003 个人中心：调 `auth.logout()` 然后 `uni.reLaunch('/pages/index/index')`
- FE-004/005/006 业务页面：所有需要登录的 API 都自然走拦截器, 401 自动处理, 不需要每个页面 try/catch
- 单测目前用 IDE 类型检查 + 手测兜底；引入 vitest 后建议至少加 store.refresh 并发去重 + token_expired 重试两条
- `auth-storage.ts` 现在和 store 双写, 是冗余设计但成本可接受 (5 个 setStorageSync per setSession). 如果未来 storage IO 成为热点, 可改为 store 单写, 拦截器读 store; 当前保留 storage 直读避免 hydrate race

---

### FE-003 · 个人中心 ✅ DONE

**目标**：登录后用户能在一个页面里看到自己的资料、邀请码、VIP 入口、邀请绑定、设置项和退出登录；点 FE-001/FE-002 留下的"FE-003 占位 toast"现在落到这里。

**改动文件**（已实装）
- `apps/mp/api/invite.ts`：`bindInvite` (BE-006) + `parseInviteError`
- `apps/mp/pages/me/index.vue`：个人中心主页，五个区块（资料卡 / VIP 占位卡 / 邀请绑定卡 / 设置区 / 退出登录）+ 顶部"工具属性"合规角标 + 鉴权 onShow 兜底
- `apps/mp/pages.json`：注册 `pages/me/index`
- `apps/mp/pages/index/index.vue`：头像点击 `gotoProfile` 从占位 toast 改为 `uni.navigateTo('/pages/me/index')`

**AC**
- [x] 头像 / 昵称 / 区域 / 邀请码（点击复制）展示
- [x] VIP 状态展示（"免费会员"卡 + 升级按钮 → modal 占位"支付通道开发中"）
- [x] 邀请绑定（BE-006，一次性）+ 7 类错误码差异化 toast + 已绑灰态
- [x] 设置项：用户协议 / 隐私政策 / 免责声明 / 关于（modal 占位，正式上架前替换 webview）
- [x] 退出登录调 `auth.logout()`（store action 内部调 BE-004 `/auth/logout` + clearSession）+ `uni.reLaunch('/pages/index/index')`
- [x] 顶部固定"本平台为信息聚合工具, 不构成投资建议"合规角标（spec/06 §法律隔离）
- [x] 未登录态 onShow 直接 `uni.reLaunch('/pages/auth/login')` 兜底

**关键设计决策**
1. **响应式订阅 + onShow 兜底双层**：
   - 主路径走 `storeToRefs(authStore)` 让 `user` / `loggedIn` 自动更新（FE-002 已铺好）
   - onShow 再判一次 `loggedIn`：用户在其它页面 `clearSession`（如 401 拦截器）后切回个人中心，需要立即弹回登录页
   - 不能在 setup 顶层判：setup 时 store 可能尚未 hydrate（理论上 Pinia 是同步的，但留 onShow 一层兜底成本几乎为零）
2. **邀请已绑状态本地缓存 + 服务端兜底**：
   - 后端 `UserPublic` 没暴露 `referrer_invite_code` 字段（在 `users.referrer_user_id` 里，不外露），所以无法从 `GET /me` 直接读出"已绑谁"
   - 方案：本地 `xgzh.invite.bound_referrer` storage key 缓存绑定后的 referrer_invite_code；进页时读出
   - 缓存丢失场景（清 storage / 换设备）：用户输入再提交时后端会抛 `invite_already_bound`，前端把这个错码翻译成"已绑定 (本机未缓存)" 并把灰态显示出来；不打扰用户继续操作，不强制重新登录
3. **邀请码输入 UX**：
   - 前端做长度校验（4-16 位）+ 自禁（输入自己的邀请码立即 toast 拦下，不浪费一次后端调用）+ 大写归一（`text-transform: uppercase` + 提交前 `.toUpperCase()`，与后端 `_normalize` 一致）
   - 7 类错误码逐个映射文案，未知错误降级到后端原文 message（兼容 BE-006 后续可能新增的错误码）
4. **退出登录二次确认**：
   - `uni.showModal` 包成 Promise 拿用户选择，避嵌套回调；`confirmColor: '#ef4444'` 用红色加强警示
   - `auth.logout()` finally 一定会 `clearSession` 不依赖网络（store 内部已写）；这里只负责 `uni.reLaunch` 回首页
   - 同时清 `KEY_BOUND_REFERRER`：避免下一个用户登录到这个设备后看到上一个用户的"已绑定"灰态
5. **VIP 卡设计**：
   - 不调用任何 API，纯前端占位；按钮点击只 modal 提示"支付通道开发中"
   - 把会员特权（AI 深度诊断 / 历史数据 / CRS 向导）写在 modal content 里，给用户做"未来想做啥"的预期
   - 视觉用金色 + 蓝色渐变与首页 hero 文字保持一致
6. **设置项目暂用 modal 而非 webview**：
   - 协议正式文本未定，先用 modal 占位放一句话，避免用户首次安装时按链接进个空白页
   - spec/08 已记录"上架前需要把三份正式文本落到 `apps/mp/static/legal/*.html` 走内嵌 webview 显示"的遗留任务
7. **不用 tabbar**：
   - 主线 IPO 列表 + 个人中心 = 2 个页面，但 FE-004 起首页会做"瀑布流 + 打新日历"复杂化，tabbar 切换会破坏 hero 头像入口的视觉统一
   - 个人中心走 navigateTo + 默认 navigationBar，用户用顶部返回键即可回去，符合"非主线页面"定位

**验收方法**

```bash
# 0. 起后端
cd apps/api && uv run uvicorn app.main:app --port 8000

# 1. 通过手机登录或微信登录拿到 access_token (FE-001 流程)

# 2. 首页头像 → 跳转个人中心
#    - 资料卡显示昵称 / 区域 / 邀请码
#    - 点击邀请码 → 提示"邀请码已复制"

# 3. 用 curl 创建第二个用户拿邀请码 (作为 referrer):
PHONE2=+8613800000111
curl -X POST http://localhost:8000/api/v1/auth/otp/send -H 'Content-Type: application/json' -d "{\"phone\":\"$PHONE2\"}"
# 从日志拿 OTP, 登录, 拿到 invite_code 比如 "XYZ123"

# 4. 在个人中心邀请绑定卡输入 "XYZ123" → 绑定 → "绑定成功"
#    - 卡片变成"已绑定邀请人 XYZ123" 灰态

# 5. 重新进个人中心: 灰态保持 (storage 缓存)

# 6. 点退出登录 → 二次确认 → 退出 → 回首页, hero 又显示"登录/注册"
#    - 检查 storage: 五个 auth.* + 一个 invite.bound_referrer 全清
```

**遗留 / 后续**
- `auth-storage` 的 `KEY_BOUND_REFERRER` 目前在 me 页本地写, 不在 stores/auth.ts 里; 等 BE 加 `GET /me/referrer` 或 `UserPublic.referrer_invite_code` 后, 可以并入 store 状态做响应式
- 设置项的 webview 替换: 等 spec/06 三份正式文本到位
- VIP 真支付通道: 微信支付 + Apple IAP, Sprint 3+ 排期
- 头像编辑 (上传 OSS) + 昵称改名 (`PATCH /me`): 需要 BE-013 OSS 鉴权和 PATCH /me 接口, 后续 PR
- 需要时可以把"我邀请了几人 / 已下首单几人"等 referrer 反向数据展示, 等 BE 增强后做

---

### FE-004 · 首页瀑布流 + 打新日历 ✅ DONE

**目标**：首页从"简单 IPO 列表"升级到结构化 IPO 信息聚合 — 今日打新置顶 + 主区列表/日历双视图 + status 多筛选 chip + 触底分页 + 数据来源声明。

**改动文件**（已实装）
- `apps/mp/api/ipo.ts`：`fetchIPOList` 升级到 BE-008 的 `page` / `size` / `status` / `industry` 完整签名；新增 `IPOStatus` / `IPOListParams` 类型；抽 `statusLabel` / `statusPalette` 给卡片色块复用
- `apps/mp/components/IPOCard.vue`：可复用卡片组件，`default` / `hero` 双密度；右上角状态色块（`subscribing` 金 / `upcoming` 蓝 / `listed` 灰 / `withdrawn` 红 / `unknown` 中性）；副标题智能切换（申购截止 / 上市日 / 申购窗口）
- `apps/mp/components/IPOCalendar.vue`：把列表按申购开始日 / 上市日 group；顶部横滚日期 chip（含数量徽标）+ 主区按日期分段列卡片；"待定"组永远沉底
- `apps/mp/pages/index/index.vue`：完整重构 — `bar` 区把市场 tab 和 视图切换分开；`status-chips` 横滚多状态筛选；列表模式头部插入"今日打新"hero 卡（最多 3 只 subscribing）；触底加载更多（hasMore 守卫）；footer aggregate 数据来源 + 免责

**AC**
- [x] 首屏分区：今日打新（hero variant 强调）+ 主列表（瀑布流）
- [x] 打新日历视图：日期轴 chip + 分组列表
- [x] status chip 筛选：全部 / 申购中 / 待上市 / 已上市
- [x] 卡片点击跳详情（保留 FE-001 的 detail 路由）
- [x] 必含 footer 免责（保留）+ 新增数据来源声明
- [x] 分页加载更多（onReachBottom，total 守卫）
- [x] 下拉刷新（onPullDownRefresh）
- [x] 切换 market / status 自动 reset 分页 + 重新拉

**关键设计决策**
1. **今日打新 = 列表里的 `subscribing` 子集**：不为它单开一个 API，避免后端字段耦合；后端 `listing_date DESC NULLS LAST` 排序天然把"近期"靠前，截前 3 个就够 hero 用。换 status filter 时今日打新区会跟着变小，符合直觉
2. **列表 / 日历 共享同一份 list 数据**：避免双拉同一份后端数据 (后端已 10min Redis 缓存，第二次拉是命中，但仍占网络往返)；日历视图基于已加载的页内做 group，用户翻到下一页才能看到下一段日期，与瀑布流分页节奏一致
3. **status 后端筛选 vs 前端筛选取舍**：选了后端筛选（query 参数 `status`）。理由：后端已有索引覆盖，total 准；前端只看局部页时筛选会显示错的"今日 X 只"。代价是切 chip 重新拉网络请求，但 BE-008 缓存就是给这个场景用的（命中率高）
4. **状态色块 spec 集中在 `api/ipo.ts`**：`statusPalette()` / `statusLabel()` 单源真相；FE-005 详情页关注按钮 / FE-006 自选页都要用同一套调色板，避免散落各处
5. **`IPOCard` 双密度而非两个组件**：`variant: 'default' | 'hero'` props 切；hero 加金蓝渐变背景 + 字号增大 + CTA 按钮区，default 紧凑；样式继承避免代码重复
6. **`IPOCalendar` 不去单独拉日历数据**：与 `items` prop 数据同源；"日期待定"组（`subscribe_start` / `listing_date` 都没有）排在最后用 `tbd` key 兜底，不丢数据
7. **副标题"申购截止 MM-DD"占位优先级最高**：subscribing 状态下用户最关心截止日；upcoming 状态下次重要看申购窗口；listed 看上市日；withdrawn / unknown 给降级文案
8. **触底分页只在列表模式生效**：日历模式下用户可能在向下滚动找日期组，不应再叠加请求；onReachBottom 内部判 `viewMode === 'list'`
9. **数据来源 footer aggregate**：从已加载 items 提取 `data_source` 集合（HK seed 是 "AKShare HK seed"，A 股是 ipos 表写入时记录的源），用 `/` 拼接展示给用户；spec/06 §3 数据来源标注硬要求
10. **没用 `wot-design-uni`**：spec/05 提到该组件库已装，但本期 IPO 卡 / 日历的样式高度定制，用原生 view + scoped scss 更稳定，避免组件库样式与项目设计 token 冲突

**验收方法**

```bash
# 0. 起后端 (会自动跑 AKShare 入库)
cd apps/api && uv run uvicorn app.main:app --port 8000

# 1. 首页 → 默认列表模式 + HK + 全部
#    - 顶部 hero / 市场 tab / 视图切换 / status chips 一行布局
#    - 今日打新区: 0-3 张 hero variant 卡片 (申购中)
#    - 主区: 瀑布流 IPOCard, 触底自动加载下一页

# 2. 切换到 A 股 → 自动 reset, 拉 A 股 IPO

# 3. 切换 status chip "申购中" → 重新拉, 主列表只剩 subscribing
#    - 今日打新区与主列表同源, 仍是同一批

# 4. 切换到日历视图 →
#    - 顶部横滚日期 chip (含数量徽标)
#    - 主区按 申购开始日 / 上市日 group, "日期待定" 沉底
#    - 点 chip 把对应组的标题高亮 (focus 反馈)

# 5. 下拉刷新 → 列表重置, 数据回到第一页
# 6. footer 显示 "数据来源：AKShare HK seed" / "AKShare A-share" 等
```

**遗留 / 后续**
- 日历 chip 点击 anchor 滚动到对应组: uniapp `scroll-view` 没有原生 anchor 支持; 需要 `scroll-into-view` 配合 `scroll-y` 容器, 而当前是页面级滚动. 先用 focus 高亮兜底, FE-005 后再 retrofit (代价是包一层 scroll-view 容器调整全局滚动)
- 行业筛选 chip: BE-008 已支持 `industry` query, 但行业枚举不在前端已知; 需要 BE 加 `GET /ipos/industries` 元数据接口或通过 items 聚合; 暂未做, FE-005 详情页加完后续做
- "今日"语义统一: 当前算 `status === 'subscribing'`, 非真"今天日期是否在 [subscribe_start, subscribe_end] 区间"; 等 BE 给一个 `is_active_today` 字段或前端基于 dayjs 比较 (但用户时区差异需要再讨论, 先用 status 简化)
- 行业图标 / 行业头像: 暂未做, 需要 spec/06 §视觉规范确认调色后批量加

---

### FE-005 · 新股详情页增强 ✅ DONE

**目标**：把详情页从"基本信息卡 + AI 诊断"扩展到"风险 banner + 基本信息 + 关注按钮 + 4-tab 深度信息 + AI 诊断（VIP 角标） + 数据来源"，并把"自选状态"集中到 Pinia store 给 FE-006 自选列表复用。

**改动文件**（已实装）
- `apps/mp/api/ipo.ts`：`fetchIPODetail` 返回从 `IPOItem` 升级到 `IPODetail`（叠加 `prospectus_url` / `sponsors` / `underwriters` / `highlights` / `risks` / `financial_summary` 6 个 BE-009 深度字段）
- `apps/mp/api/favorites.ts`（新建）：`addFavorite` / `removeFavorite` / `listFavorites` + `parseFavoriteError`，1:1 BE-010 schema；`code` 永远带市场后缀，前端只持一份标识
- `apps/mp/stores/favorites.ts`（新建）：Pinia store，集中持自选列表 + `isFavored(code)` O(1) 查询；乐观更新 + 失败回滚；watch `auth.loggedIn` 翻假后自动 `reset()`，防止跨用户串数据
- `apps/mp/components/FavoriteButton.vue`（新建）：未登录点击弹 modal 引导跳登录；已登录调 store action 乐观切换；`favorite_code_invalid` / 网络错误分类 toast；`size: default | compact` 给详情页 / 列表卡片复用
- `apps/mp/pages/ipo/detail.vue`：完整重构 — 顶部红色风险 banner + Header（名称 / status badge / 关注按钮）+ 6 格基本信息卡 + 4 tab（基本面 / 保荐承销 / 亮点 / 风险）+ AI 诊断 CTA（"VIP 限免"角标占位）+ 数据来源行 + 免责行

**AC**
- [x] 顶部基本信息卡 + 关注按钮（接 BE-010）
- [x] 财务摘要 / 保荐人 / 亮点 / 风险 4 个 tab
- [x] AI 诊断按钮（已有）保留并加 VIP 配额提示（CTA 角标 `VIP 限免`，真配额逻辑后续 BE 落地）
- [x] 必含 IPO 风险提示 banner（顶部固定红色 banner）
- [x] 关注按钮乐观更新 + 失败回滚 + 错误码分类 toast
- [x] 未登录访问详情仍可看 + AI 诊断仍可点（匿名允许调用 agent）
- [x] 详情 404 兜底文案"该新股暂未在数据源命中, 仍可使用 AI 诊断"
- [x] 招股书链接：MP-WEIXIN 平台引导复制链接到浏览器（小程序内 PDF 受限）

**关键设计决策**
1. **`IPODetail` extends `IPOItem`**：列表用浅版本，详情多 6 个深度字段；客户端可以用同一份 store 缓存 list, 详情接口只补 delta，避免重复传字段。`highlights` / `risks` 第一刀允许为空（BE-018 RAG 后续填）
2. **关注状态走 Pinia store, 不在 `IPODetail` 里**：BE-009 的 `IPODetail` 不带 `favored` 标志位（不耦合 user 信息），前端登录态首次进详情触发 `useFavoritesStore().loadOnce()`，后续 add/remove 只内存更新 + API 同步；FE-006 自选 Tab 直接读同一份 store，不重复拉
3. **乐观更新 + 失败回滚**：`add` 立即把 placeholder item 塞 `items[]` 头部（用前端推导的 market），UI 立刻变"已关注"；API 失败则从 `items[]` 删除，UI 翻回"未关注"；`remove` 先删除再调 API 失败再插回原位置。这样用户感知操作是即时的，不卡 200~500ms 网络
4. **未登录点击关注 → modal 引导**：不直接静默失败也不强制阻断浏览。modal 文案"登录后才能收藏"，确认跳登录页。这与 spec/04 §1.3 的"匿名也能用 80% 功能, 涉及个性化才登录"对齐
5. **错误码分类 toast**：`favorite_code_invalid` 提示"股票代码格式不支持"（HK seed 中带港股 5 位 code 后缀的特殊用例）；`token_*` 由 `utils/request.ts` 拦截器处理（silent refresh / 跳登录），按钮内不再 toast；其他网络错误降级到通用文案
6. **跨 store 联动用 watch, 不用反向 import**：让 `favorites` store 内部 `watch(authStore.loggedIn)`，登出自动 reset。如果让 auth store 主动 import favorites store，会形成 `auth → favorites → api/favorites → utils/request → auth` 的隐式循环，watch 方案让箭头单向 favorites → auth，符合"业务 store 依赖底层鉴权 store"的层级
7. **4 tab 而非长卡片堆叠**：基本面 / 保荐承销 / 亮点 / 风险信息密度差异大（基本面是结构化 KV, 亮点 / 风险是 bullet list, 保荐承销是 chip + 链接），统一卡片堆叠会让用户被动滚很多空白；横向 tab 让用户主动选切，每 tab 自己空态文案"暂未补齐"
8. **财务摘要 dict 渲染容错**：`financial_summary: dict[str, Any]` 后端可加新字段不需要前端改；`labelMap` 给已知 9 个字段做 i18n，其他降级原 key；数值字段按 key 自动选格式化（比率 → `%`, 大数字 → `亿 / 万`）
9. **VIP 角标占位而非真配额**：spec/08 AC 写"加 VIP 配额提示"。当前没有 VIP 状态字段，直接做配额限制需要 BE 加 `GET /me/quota` 接口；先 CTA 上贴 "VIP 限免" 角标占位，AI 诊断照常匿名能调，后续 BE 落地配额时只在按钮的 onTap 加 `if (quota.exhausted)` 拦截
10. **status palette 复用 `api/ipo.ts`**：详情页 header 的 status badge 配色直接调 `statusPalette()`，与 FE-004 列表卡 / FE-006 自选页同源，不再散落

**验收方法**

```bash
# 0. 起后端
cd apps/api && uv run uvicorn app.main:app --port 8000

# 1. 未登录态进详情页
#    - 顶部红色风险 banner 看得到
#    - 6 格基本信息卡渲染
#    - 关注按钮空心 ☆ + "关注"
#    - 点关注 → 弹 modal "登录后才能收藏" / "去登录"
#    - 4 tab 切换正常, 数据未补齐时显示"暂未补齐"
#    - AI 诊断 CTA 上有 "VIP 限免" 角标, 点击进 agent 页

# 2. 登录态再进详情页
#    - 关注按钮立即显示当前是否已关注 (loadOnce 拉取后)
#    - 点关注 → 立即变 ★ "已关注" + toast; 再点 → 变回 ☆ + toast
#    - 切到自选 Tab (FE-006 落地后) 看到刚关注的 IPO

# 3. 后端注入 highlights / risks (示例)
psql xgzh -c "UPDATE ipos SET extra = jsonb_set(coalesce(extra, '{}'::jsonb), '{highlights}', '[\"营收 3 年 CAGR 35%\", \"行业领先\"]'::jsonb) WHERE code = '0700.HK';"
#    - 投资亮点 tab 显示 2 条 bullet
#    - bullet 左侧绿色 +
```

**遗留 / 后续**
- BE 加 `GET /me/quota` 后, AI 诊断 CTA 显示当日剩余次数 + 用尽时 modal 引导升级 VIP
- 财务摘要 BE-009 当前从 `extra` JSONB 读, BE-018 招股书 RAG 落地后可以自动补齐 `highlights` / `risks`
- 招股书 PDF 渲染 (MP-WEIXIN): 当前是引导用户复制链接, 后续做"招股书要点 RAG 摘要"页面替代直接渲染 PDF (小程序原生不支持 PDF, webview 也有限制)
- `FavoriteButton compact` 视觉密度: 已预留 size prop, FE-006 自选列表卡片角标 / 长按移除时复用; 当前还没场景调用

---

### FE-006 · 自选列表 Tab ✅ DONE

**目标**：把"我的自选"做成完整页面 — 列表卡片复用 `IPOCard` + 长按二次确认移除 + 空态引导回首页 + 个人中心入口（含数量徽标），并验证 FE-005 的 `useFavoritesStore` 在多页面间响应式同步。

**改动文件**（已实装）
- `apps/mp/pages/me/favorites.vue`（新建）：自选列表页 — 顶部 stats 条（已关注 N / 申购中 X）+ `IPOCard` 列表（适配器把 `FavoriteItem` 转 `IPOItem` 形状）+ 长按 ActionSheet → modal 二次确认 → store 移除 + 空态（图标 + 文案 + "去发现新股"按钮）+ 错误态点击重试 + 下拉刷新（`loadOnce(force=true)`）
- `apps/mp/pages.json`：注册 `/pages/me/favorites` 路由 + `enablePullDownRefresh: true`
- `apps/mp/pages/me/index.vue`：插入"我的自选"入口卡片（VIP 卡下方，邀请绑定上方），右侧显示自选数量徽标；进个人中心时预热 `favStore.loadOnce()`，徽标即时显示

**AC**
- [x] 列表显示用户全部自选（`useFavoritesStore().items` 响应式）
- [x] 长按移除（ActionSheet → modal 二次确认）
- [x] 空态有引导文案（"去发现新股"按钮跳首页）
- [x] 顶部 stats 条引导用户行动（"申购中 X 只"用金色高亮）
- [x] 下拉刷新强刷自选列表（`loadOnce(force=true)`）
- [x] 个人中心"我的自选"入口 + 数量徽标
- [x] 未登录访问 favorites 页直接 `reLaunch` 回登录（不能 navigateTo, 防后退栈）
- [x] 跨页响应式：详情页 ☆/★ 切换 → 自选列表立即同步（store 单源真相）

**关键设计决策**
1. **复用 `IPOCard` 而非新写一个卡片**：自选页的卡片视觉要和首页一致（用户认知一致），通过 `toIPOItem(f: FavoriteItem)` 适配器把 `LEFT JOIN ipos` 投影里缺失的 `subscribe_start` / `subscribe_end` / `pe_ratio` 等填 null，`IPOCard` 内部已对 null 兜底渲染 `--` / "信息待补"
2. **长按移除 = ActionSheet + modal 二次确认 双层兜底**：单 modal 用户长按可能误触；ActionSheet 给"取消关注"红色按钮当第一道意图确认，确认后再 modal 二次确认（含具体 IPO 名 `${item.name}`）；移除走 `favStore.remove`，乐观更新 + 失败回滚由 store 内部完成
3. **数据来源 = `useFavoritesStore` 单源真相**：详情页 `★` 关注 → store 乐观更新 `items[]` → 自选列表 storeToRefs 响应式立即同步多一项；详情页 `☆` 取消 → 自选列表自动少一项；不需要返回时 reload。这是 FE-005 选 Pinia store 集中持自选的核心收益
4. **HK seed 收藏的 placeholder 渲染**：用户可以收藏"还没入 ipos 表的 HK seed IPO"，此时 `name` / `industry` / `issue_price` 都为 null；adapter 用 `code` 兜底 `name`，`IPOCard` 显示"行业未分类 / --" 灰态卡片，不丢条目
5. **空态文案 + CTA 引导**：仿 spec/06 §UX 规范的"empty state 必有 CTA"原则；不只是文案，还有大按钮跳首页发现新股；图标用 ☆ 与详情页关注按钮符号呼应
6. **stats 条把"申购中"高亮**：用户进自选页最关心"我关注的哪些今天能打"，金色卡片让用户瞄一眼就知道 actionable 数量；非申购中的归到"已关注"灰态总数
7. **个人中心入口数量徽标**：从 `favStore.items.length` 派生 computed，进个人中心 `loadOnce` 触发后徽标自动渲染；> 0 才显示徽标，避免"0"占位
8. **下拉刷新 stopPullDownRefresh**：UniApp `enablePullDownRefresh + onPullDownRefresh` 必须显式 `stopPullDownRefresh`，否则 loading 圈不会消失（小程序原生交互细节）
9. **adapter 不放在 `api/favorites.ts`**：`toIPOItem` 是渲染层适配，跟 BE 数据模型无关；放页面里清晰"这是 favorites 页 → 复用 IPOCard 的胶水"，而非污染 API client
10. **未做长按拖拽排序 / 批量移除**：Sprint 1 规模 (单用户预期 < 50 自选) 不需要；后续用户量大时再加 spec/05 §自选优化的"拖拽排序"功能

**验收方法**

```bash
# 0. 起后端
cd apps/api && uv run uvicorn app.main:app --port 8000

# 1. 登录后, 进任意 IPO 详情页, 点 ☆ 关注 (FE-005 已实现)
# 2. 返回首页, 点右上角头像进个人中心
#    - 看到"我的自选"卡片, 右侧徽标显示 1
# 3. 点"我的自选"卡片, 进自选列表页
#    - 顶部 stats: 已关注 1 / 申购中 0 (或 1, 取决于该 IPO status)
#    - 列表显示刚才关注的 IPO 卡片 (复用首页样式)
# 4. 长按卡片
#    - ActionSheet 弹"取消关注"红色按钮
#    - 点确认 → modal "确认取消关注 ${name}?"
#    - 点"取消关注" → toast "已取消关注", 列表立即少一项
# 5. 列表为空 → 显示空态 + "去发现新股" 按钮 → 点击回首页
# 6. 下拉刷新 → 加载圈出现, 完成后自动收回
# 7. 在详情页再次关注 → 返回自选列表, 立即看到新增项 (响应式)
```

**遗留 / 后续**
- 申购窗口推送提醒（`notify_on_subscribe` 字段当前都是默认 true，但前端没有"开关"切换 UI）：等 BE-011 push 真接入 APNs / FCM 后，加每行卡片右侧"🔔 / 🔕"小开关，调 BE-010 的"PUT /favorites/{code}/notify"或"重新 add 覆盖"
- 排序选项（按 favorited_at / 按 subscribe_start / 按 status）：当前固定后端 `favorited_at DESC`，量大后加排序 chip
- 批量移除 / 多选模式：Sprint 1 规模不需要，后续做
- 自选数 > 100 后的分页：BE-010 当前不分页，后端加 `?page=&size=` 参数后前端走 `onReachBottom`
- `notify_on_subscribe` 在 add 时 UI 没暴露开关：前端默认传 true，符合用户"我关注就要提醒"的直觉，后续如果要"静默关注"可以在 ActionSheet 加多一项

---

### QA-001 · API 集成测试套件 ✅

**改动文件**
- `apps/api/tests/integration/__init__.py`（新, 空）
- `apps/api/tests/integration/conftest.py`（共享 fixtures: PG schema reset + InMemoryRedis + Mock SMS + fake LLM + 一站式 `client`）
- `apps/api/tests/integration/test_e2e_ipo_diagnose.py`（一条主用例 + 两条退化用例）

**AC**
- [x] 一条 e2e 用例：注册 → 拿 token → /me → /ipos 列表 → /ipos/{code} 详情 → /agent/diagnose SSE → 收藏闭环；校验 start/delta/end SSE 帧结构 + 合规免责声明 ("不构成投资建议") + LLM mock token 透传无丢失
- [x] CI 上能用 docker-compose 起 PG/Redis 跑通：`make infra-up` 起 `xgzh-postgres` + `xgzh-redis`，再 `createdb xgzh_test` + `XGZH_TEST_DATABASE_URL=...` 跑 pytest（步骤见 `apps/api/README.md` §测试）
- [x] 没配 `XGZH_TEST_DATABASE_URL` 时整个 integration session 自动 skip（顶层 `tests/conftest.py` 的 `db` marker hook），CI 不会因没起 DB 红
- [x] LLM mock 走 `fake_llm` fixture，monkey-patch `llm_client.stream_chat` 返回固定 5 段 token + 真实 `DISCLAIMER` 字符串，CI 完全不打远程 LLM 也不依赖 SILICONFLOW_API_KEY
- [x] `uv run pytest` 全绿（211 个用例，1.25s 跑完 3 条 e2e）

**关键设计决策（与现有 `tests/test_*.py` 的关系）**

- 现有 `tests/test_favorites.py` / `test_ipos_list.py` 等"单功能"测试当前把 fixtures 内联在自己文件里。本次只把"复合 e2e"需要的 fixtures 抽到 `tests/integration/conftest.py`，**不动**现有文件以保持 diff 最小。后续 sprint 可分阶段把单功能测试也迁过来共享 fixtures。
- 发现并修了一个隐藏 bug：`patch_session_factory` 之前只 patch `app.db` + `ipo_ingest_service`，没 patch `ipo_service`。因为 `ipo_service` 在 module-level 已经把 `get_session_factory` 拷到自己 namespace，改 `app.db` 不传染。e2e 走 `/api/v1/ipos` 列表时这个漏洞才暴露——表现是测试库 seed 完测不到。conftest 现在三处都 patch（`db_pkg` / `ingest_mod` / `ipo_service_mod`），未来加新 service 要复制一份。
- LLM 选 monkey-patch `stream_chat` 而不是 `acompletion`：前者是测试边界（service 层契约），后者是 litellm 实现细节，contract test 应当在更稳的边界。代价是 e2e 不覆盖 `stream_chat` 内部的 `forbidden_pattern_filter` + `ensure_disclaimer` —— 这些已在 `test_compliance.py` 单测覆盖，e2e 改测"路由 + SSE 帧序列 + 免责声明端到端透传"。
- SSE 用 `httpx.ASGITransport` 走非流式收 body 后 `_parse_sse_frames` split：`EventSourceResponse` 在 ASGI 直连下会 buffer 全部 chunks 再关连接，对 e2e 完全够用；真流式断流（client 中途断）测试在 Sprint 2 RAG 落地时再补。

**手测脚本（本地一键复现）**

```bash
# 1. 起基础设施 (PG + Redis)
cd xgzh/infra && docker compose up -d postgres redis

# 2. 准备测试库 (与 dev 库同实例不同库, 防误清)
psql -U xgzh -h localhost -d postgres -c 'CREATE DATABASE xgzh_test'

# 3. 跑 e2e
cd ../apps/api
XGZH_TEST_DATABASE_URL='postgresql+asyncpg://xgzh:xgzh_dev_pass@localhost:5432/xgzh_test' \
  uv run pytest tests/integration/ -v
```

预期: `3 passed in ~1.2s`。

**已知限制 / 下一步**

- 当前 e2e 未覆盖 BE-007 调度任务（APScheduler 周期性 ingest）— 那是后台任务，e2e 难以稳定 trigger，单测 `test_ipo_ingest.py` 已覆盖逻辑。
- `test_e2e_diagnose_anonymous_allowed` 这条用例当前 PASS = 默认行为（spec/04 §1.3 允许匿名调 `/agent/diagnose`）。如果 Sprint 2 改为"登录强制 + 配额限制"，这条用例会 fail 提醒同步更新 spec 文档。
- ~~测试库还需要手动 `createdb xgzh_test` 一次~~ → ✅ Sprint 1.5 收尾包 `make test-db-init` 已自动化（含 pgcrypto extension 安装），详见本文档 §Sprint 1.5

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

> 然后进入 Sprint 2（AI Agent + RAG），spec/07 / spec/09 里有完整任务。

---

## 🧹 Sprint 1.5 收尾包 ✅ — 跨 Sprint 1/2 的"破窗"清理

> 定位：Sprint 1 验收完成后、Sprint 2 起跳前的**轻量收尾**。
> 目标：把 Sprint 2 真正会复用的 Sprint 1 遗留扫干净，避免被 RAG 主战场带出连锁问题。
> 节奏：1 个 PR，~3 小时。**不做** Sprint 3+ 才会触发的遗留（律师文本 / 真支付 / OSS 等）。

### Sprint 1.5 · 缓存失效 hook + Makefile DX 整理

**改动文件**

```
apps/api/app/cache/redis_client.py             # +RedisClientProtocol.delete_by_prefix
                                               #  + RealRedisClient.delete_by_prefix (SCAN+UNLINK)
                                               #  + InMemoryRedisClient.delete_by_prefix (dict 遍历)
apps/api/app/cache/__init__.py                 # +invalidate_namespace(*namespaces) helper + export
apps/api/app/services/ipo_ingest_service.py    # run_ingest_a_job 末尾 await invalidate_namespace
apps/api/tests/test_cache.py                   # +7 条单测 (delete_by_prefix x2 + invalidate_namespace x5)
apps/api/Makefile                              # 新建: help / test-db-init / test-unit / test-e2e / test-all / lint / typecheck
spec/08-sprint-1-backlog.md                    # 三处遗留勾 ✅ + 本章节
spec/09-sprint-2-backlog.md                    # +BE-S2-000 HK ingest (作为 BE-S2-004 前置) + Sprint 1.5 注脚
```

**AC**

- [x] `RedisClientProtocol.delete_by_prefix(prefix: str) -> int`：抽象接口加一刀，真 Redis / InMemory 双端实现
- [x] 真 Redis 用 `SCAN match=xgzh:<prefix>* count=500` + `UNLINK`（非阻塞 DEL；老版本 fallback 到 DEL）
- [x] InMemory 用 dict 遍历 + `startswith` 删除
- [x] `cache.invalidate_namespace("ipos:list", "ipos:detail")`：按 `@cached(namespace=...)` 批量清；逻辑前缀 `cache:<ns>:` 带冒号边界，不会误删 `ipos:list-ext`
- [x] **fail-soft**：单个 namespace client 抛异常时 catch + warn，其它 namespace 继续清；总函数不抛（让 ingest 任务"成功落库"状态不被缓存失效失败拖垮）
- [x] `run_ingest_a_job` 末尾接入；返回值新增 `cache_invalidated: int` 统计字段（不影响现有调用方对 `received/inserted/updated` 的断言）
- [x] **`Makefile`** 提供 7 个一行命令：`help` / `test-db-init`（幂等 createdb + pgcrypto extension）/ `test-unit` / `test-e2e` / `test-all` / `lint` / `typecheck`
- [x] `make test-db-init` 实测：第一次创建库 + 装 extension；第二次 noop 不报错
- [x] `make test-e2e` 实测：3 e2e 用例 2.1s 跑通
- [x] 全套 `uv run pytest` 218 passed（211 → 218，新增 7 条 cache 单测）
- [x] `uv run mypy app/cache/` / `uv run ruff check app/cache app/services/ipo_ingest_service.py tests/test_cache.py` 我新增代码 0 报错（pre-existing baseline 保持不变）

**关键设计决策**

1. **新加抽象接口而不是装饰器层 hard-code**：让 `delete_by_prefix` 进 `RedisClientProtocol`，未来 Sprint 3+ 文章流水线 / 评测集 ingest 也能直接 reuse；如果只在 `cache/__init__.py` 里 `if isinstance(client, InMemoryRedisClient)` 分支处理，会在 protocol-based 设计哲学上倒退。
2. **逻辑前缀必须带冒号** (`cache:<ns>:`)：装饰器写入时是 `cache:<ns>:<func>:<hash>`，天然有冒号边界。如果删 `cache:ipos:list`（无尾冒号）会误删 `cache:ipos:list-ext` / `cache:ipos:listing`。这条不变量在 `test_inmemory_delete_by_prefix` 里用真用例锁定。
3. **fail-soft 而不是 fail-fast**：缓存失效失败不应让 ingest 整个任务被 scheduler 标 failed 后停掉。最差就是用户看到 stale 10/30 min 的数据，业务可降级；但每一次失败都 log warn 让运维可追。这与 BE-007 `run_ingest_a_job` "永不抛" 的整体设计一致。
4. **不在 `delete_by_prefix` 里 catch 异常**：异常向上传到 `invalidate_namespace`，让上层决定是否吞；如果 client 层就把异常吞了，调用方分不清"删了 0 个"和"出错了"。这在 `test_invalidate_namespace_fail_soft_on_client_error` 里用 `BoomClient` 子类验证。
5. **`Makefile` 用 BSD/GNU 兼容的 grep 正则**：`[a-zA-Z0-9_-]+:` 必须含数字（`test-e2e` 中的 `2`），否则 `make help` 会神秘漏 target；这条踩过坑后写注释提醒。
6. **测试库自动化只装 pgcrypto，不装 pgvector**：pgvector 是 Sprint 2 BE-S2-003 才需要的扩展，Sprint 1.5 范围只覆盖 Sprint 1 已有需求；Sprint 2 BE-S2-003 PR 把 `pgvector` 加进 Makefile target 即可。
7. **不动 `test_ipo_ingest.py` 已有 fixture**：3 条 happy / fetch_error / empty 测试不强绑定 `cache_invalidated` 字段，只读 `received/inserted/errors`。本 PR 验证它们仍 PASS（实际跑了一遍）。后续 Sprint 2 真要测"ingest 完后 list_ipos 立刻拿新数据"再单独加 e2e 用例。
8. **`make test-e2e` 与 `make test-all` 区分**：CI 用 `test-all`（单元 + 集成），本地快速反馈用 `test-unit`（不依赖 PG，~5s）；e2e 单独跑用 `test-e2e`（~3s）。`test` 默认 alias 到 `test-all` 让"我要全跑"的直觉一发命中。

**Sprint 1.5 给 Sprint 2 留下的复用价值**

- BE-S2-000 HK ingest → 直接复用 `invalidate_namespace`，HK 写完 ipos 表立刻让缓存回源
- BE-S2-007 LangGraph 主循环依赖 `get_ipo_detail` 拉 RAG context → 缓存永远新鲜，不会出现"AI 给的发行价是 30 min 前的"
- QA-S2-001 / QA-S2-002 评测 CI → `make test-db-init` + `make test-e2e` 两条命令搞定环境，CI yaml 不再需要散在 README 的多步操作
- Sprint 3+ 文章流水线 / 评测集 ingest → `cache.invalidate_namespace` 可以直接 reuse，不需要每次重新发明轮子

**手测脚本**

```bash
# 1. 测试库初始化（幂等）
cd apps/api
make test-db-init
# → ==> 创建测试库 xgzh_test (如已存在则跳过)...
# → ==> 安装 pgcrypto extension (gen_random_uuid() 需要)...
# → ==> 测试库就绪: postgresql+asyncpg://xgzh:xgzh_dev_pass@localhost:5432/xgzh_test

# 2. 跑全部测试 (CI 等价命令)
make test-all
# → ============================== 218 passed in 24.85s =============================

# 3. 单跑 e2e
make test-e2e
# → 3 passed in 2.15s

# 4. 验证缓存失效 hook 真生效（手测）
uv run python -c "
import asyncio
from app.cache import invalidate_namespace, get_redis_client

async def main():
    c = get_redis_client()
    # 假设之前 list_ipos 已被调过, 缓存里有 xgzh:cache:ipos:list:* 这种 keys
    removed = await invalidate_namespace('ipos:list', 'ipos:detail')
    print(f'removed {removed} keys')

asyncio.run(main())
"
```

**已知限制 / 不做**

- HK ingest 真源接入：1d 估时超出 Sprint 1.5 的 ~3h 范围，已升级为 **BE-S2-000** 编号管理（spec/09 已加），与 BE-S2-001 并行起跑，BE-S2-004 前必须完成
- pgvector extension 安装：留给 BE-S2-003 PR 统一处理，避免 Sprint 1.5 装了 Sprint 2 不用而徒增复杂度
- `pyproject.toml` `[tool.ruff]` baseline 收紧：51 个 pre-existing ruff error（大多是 `N818` Exception 命名 / `F401` import 未用）由 Sprint 2 内 BE-S2-007 / BE-S2-009 各自顺手清，不在 Sprint 1.5 范围
- `pyproject.toml` `[tool.mypy]` baseline 收紧：24 个 pre-existing mypy error（多在 redis-py / litellm 的 stub 不全），同上由 Sprint 2 各 PR 顺手处理
