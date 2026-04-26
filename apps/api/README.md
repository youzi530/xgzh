# xgzh-api

XGZH (新股智汇) FastAPI 后端。

## 当前能力（Sprint 0 + INFRA-001/002 + BE-001/002/003/004/005/006/007/008）

API:

- `GET /healthz` 健康检查
- `GET /api/v1/ipos?market=A&status=listed&industry=信息技术&page=1&size=20` A 股新股列表（走 `ipos` 表，BE-007 调度入库；`status` / `industry_l1` 精确筛选；分页 size 1-100；`listing_date DESC NULLS LAST` 排序；Redis 缓存 10min，BE-008）
- `GET /api/v1/ipos?market=HK` 港股新股列表（akshare 暂用 seed，HKEX/Futu 接入排 Sprint 2；同 schema 支持 status/industry/page/size）
- `GET /api/v1/ipos/{code}` 新股详情（A/US 走 DB；HK 走 seed）
- `POST /api/v1/agent/diagnose` AI 一键诊断（DeepSeek-V3 SSE 流式）
- `POST /api/v1/auth/otp/send` 手机号 OTP 发送（dev 走 Mock SMS，60s 限流，5min TTL）
- `POST /api/v1/auth/login/phone` OTP 校验 + 自动注册 + 颁发 access/refresh JWT（5/5min 限流）
- `POST /api/v1/auth/login/wechat-mp` 微信小程序 code → openid/unionid → 注册/登录 + JWT（同 code 5/min 限流）
- `POST /api/v1/auth/refresh` Refresh token rotation：旧 refresh 拉黑 + 颁发新 access+refresh（5/min 限流）
- `POST /api/v1/auth/logout` 拉黑当前 access（+ 可选拉黑 refresh），需 `Authorization`
- `GET /api/v1/me` 当前用户基本信息（需 `Authorization: Bearer <access_token>`）
- `POST /api/v1/invite/bind` 绑定邀请人（一次性，需登录，10/min/user 限流）

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

Schema（Alembic `0001_init`，PG 16 + pgvector 0.8.2）:

- `users` / `auth_sessions` / `invite_codes`
- `ipos` / `ipo_documents`（embedding `vector(1024)` + HNSW 索引）
- `user_favorites` / `push_tokens`

缓存层（`app/cache/`）:

- `@cached(ttl_seconds=N, namespace="...")` — JSON 序列化的函数级缓存
- `@rate_limit(times=N, per_seconds=N, key_func=...)` — Lua 原子 INCR+EXPIRE 限流
- `RealRedisClient` 走真 Redis；`InMemoryRedisClient` 走 dict+asyncio.Lock（单测/降级用）
- 所有 key 自动加 `xgzh:` 前缀
- `RateLimitExceeded` 由 `main.py` 全局 handler 转 HTTP 429（带 `Retry-After` header）

```python
from app.cache import cached, rate_limit, RateLimitExceeded

@cached(ttl_seconds=1800, namespace="ipo")
async def fetch_ipo_basic(code: str) -> dict: ...

@rate_limit(
    times=1, per_seconds=60, namespace="otp",
    key_func=lambda phone: f"phone:{phone}",
)
async def send_otp(phone: str) -> None: ...
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

```bash
# 单测（无 DB 集成测试自动跳过）
uv run pytest

# 含 DB 集成测试（迁移 up/down/idempotent）
# 先建测试库 xgzh_test:
psql -U postgres -c "CREATE DATABASE xgzh_test OWNER xgzh;"
psql -U postgres -d xgzh_test -c "CREATE EXTENSION pgcrypto; CREATE EXTENSION vector;"

XGZH_TEST_DATABASE_URL='postgresql+asyncpg://xgzh:xgzh_dev_pass@localhost:5432/xgzh_test' \
  uv run pytest -q
```

## 项目结构

```
app/
├── api/v1/         # 路由 (ipos / agent / auth / me / invite)
├── core/           # 配置、日志
├── services/       # 业务逻辑 (ipo / agent / otp / user / auth / invite)
├── adapters/       # 外部数据源 / 通道
│   ├── akshare_client.py
│   ├── llm_client.py
│   ├── sms/        # SMS 通道 (base / mock / aliyun / factory)
│   └── wechat/     # 微信小程序 jscode2session (BE-005)
├── security/       # JWT 颁发 / 解析 + FastAPI 鉴权依赖 + 黑名单
│   ├── jwt.py        # HS256 access + refresh, 严格 typ 隔离
│   ├── deps.py       # get_current_user / get_optional_user
│   └── blacklist.py  # jti 粒度黑名单 (Redis SETEX, fail-open 读)
├── schemas/        # Pydantic 模型 (ipo / agent / auth)
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
