"""BE-004: refresh + logout + 黑名单 端到端测试.

覆盖:
- POST /auth/refresh
  - 200 happy: 旧 refresh 拉黑, 新 access/refresh 可用
  - 200 chain: 新 refresh 还能再 refresh 一次 (rotation 链)
  - 401 token_invalid: access 当 refresh / 篡改 / 垃圾
  - 401 token_expired: 已过期 refresh
  - 401 token_revoked: 旧 refresh (已 rotation) 复用
  - 401 token_revoked: logout 后再用 refresh
  - 401 user_unavailable: 用户软删 / 禁用
- POST /auth/logout
  - 200 + revoked_access=True + revoked_refresh=True (带 refresh body)
  - 200 + revoked_access=True + revoked_refresh=False (不带 body)
  - logout 后立刻调 /me → 401 token_revoked (access 黑名单生效)
  - 401 logout 不带 Authorization
  - 200 + revoked_refresh=False: 试图 logout 别人的 refresh (sub 不一致)
- 限流: 同 refresh 1 分钟 5 次, 第 6 次 429
- 黑名单单元: blacklist_jti / is_jti_blacklisted / TTL=0 静默不写

依赖真 PG (XGZH_TEST_DATABASE_URL), 标 ``@pytest.mark.db``。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.adapters.sms import (
    MockSMSAdapter,
    reset_sms_adapter,
    set_sms_adapter,
)
from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.core.config import Settings, get_settings
from app.db.base import get_session
from app.db.models import User
from app.main import create_app
from app.security import (
    blacklist_jti,
    create_refresh_token,
    is_jti_blacklisted,
)
from app.services import otp_service

pytestmark = pytest.mark.db


# ------------------- DB / fixture (与 test_me / test_auth_login 同源) -------------------


def _build_alembic_config(test_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)
    return cfg


async def _drop_business_tables(url: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            )
            for (tbl,) in rows:
                await conn.execute(text(f'DROP TABLE IF EXISTS public."{tbl}" CASCADE'))
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
async def schema_at_head(test_database_url: str) -> AsyncIterator[str]:
    await _drop_business_tables(test_database_url)
    cfg = _build_alembic_config(test_database_url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    yield test_database_url


@pytest.fixture
async def db_engine(schema_at_head: str):
    engine = create_async_engine(schema_at_head, future=True)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=db_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def truncate_users(db_engine) -> AsyncIterator[None]:
    async with db_engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE users, auth_sessions, user_favorites RESTART IDENTITY CASCADE")
        )
    yield


@pytest.fixture
async def redis_client() -> AsyncIterator[InMemoryRedisClient]:
    client = InMemoryRedisClient()
    set_redis_client(client)
    yield client
    await client.aclose()
    reset_redis_client()


@pytest.fixture
async def mock_sms() -> AsyncIterator[MockSMSAdapter]:
    adapter = MockSMSAdapter()
    set_sms_adapter(adapter)
    yield adapter
    reset_sms_adapter()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_users: None,  # noqa: ARG001
    redis_client: InMemoryRedisClient,  # noqa: ARG001
    mock_sms: MockSMSAdapter,  # noqa: ARG001
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ------------------- helpers -------------------


async def _login(
    client: httpx.AsyncClient, phone: str = "+8613800138000", code: str = "123456"
) -> dict:
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    r = await client.post(
        "/api/v1/auth/login/phone", json={"phone": phone, "code": code}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _refresh(client: httpx.AsyncClient, refresh_token: str) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )


# ------------------- 200 happy + chain -------------------


async def test_refresh_returns_new_token_pair(client: httpx.AsyncClient) -> None:
    body = await _login(client, phone="+8613800138001")
    old_access = body["tokens"]["access_token"]
    old_refresh = body["tokens"]["refresh_token"]

    r = await _refresh(client, old_refresh)
    assert r.status_code == 200, r.text
    new_tokens = r.json()
    assert new_tokens["token_type"] == "Bearer"
    assert new_tokens["access_token"] != old_access
    assert new_tokens["refresh_token"] != old_refresh

    # 新 access 立刻可用
    me = await client.get("/api/v1/me", headers=_bearer(new_tokens["access_token"]))
    assert me.status_code == 200


async def test_refresh_chain_is_supported(client: httpx.AsyncClient) -> None:
    body = await _login(client, phone="+8613800138002")
    refresh1 = body["tokens"]["refresh_token"]

    r2 = await _refresh(client, refresh1)
    assert r2.status_code == 200
    refresh2 = r2.json()["refresh_token"]

    r3 = await _refresh(client, refresh2)
    assert r3.status_code == 200
    assert r3.json()["refresh_token"] != refresh2


# ------------------- 401 各路径 -------------------


async def test_refresh_with_garbage_returns_401_invalid(
    client: httpx.AsyncClient,
) -> None:
    # 注意: schema min_length=10 → 短串走 422; 这里给一个长度足够的垃圾 token, 让它进路由
    r = await _refresh(client, "not.a.valid.jwt.payload.zzz")
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["code"] == "token_invalid"


async def test_refresh_with_too_short_token_returns_422(
    client: httpx.AsyncClient,
) -> None:
    """Pydantic min_length=10 在路由前拦截, 不进 service. 422 是预期."""
    r = await _refresh(client, "short")
    assert r.status_code == 422


async def test_refresh_with_access_token_returns_401_invalid(
    client: httpx.AsyncClient,
) -> None:
    body = await _login(client, phone="+8613800138003")
    access = body["tokens"]["access_token"]
    r = await _refresh(client, access)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_invalid"


async def test_refresh_with_tampered_returns_401_invalid(
    client: httpx.AsyncClient,
) -> None:
    body = await _login(client, phone="+8613800138004")
    refresh = body["tokens"]["refresh_token"]
    # ⚠️ 不能简单改最后一个字符: base64url 的最后字符低 bits 可能是 padding, 改不到签名位.
    # 改签名段中部 (倒数第 8 位) 一定改到真签名 bits.
    pivot = -8
    orig = refresh[pivot]
    bad = refresh[:pivot] + ("A" if orig != "A" else "B") + refresh[pivot + 1 :]
    r = await _refresh(client, bad)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_invalid"


async def test_refresh_with_expired_token_returns_401_expired(
    client: httpx.AsyncClient,
) -> None:
    body = await _login(client, phone="+8613800138005")
    user_id = uuid.UUID(body["user"]["user_id"])

    base = get_settings()
    expired_settings = Settings(
        jwt_secret=base.jwt_secret,
        jwt_algorithm=base.jwt_algorithm,
        jwt_issuer=base.jwt_issuer,
        jwt_audience=base.jwt_audience,
        jwt_refresh_ttl_seconds=-10,
    )
    expired, _ = create_refresh_token(user_id, settings=expired_settings)
    r = await _refresh(client, expired)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_expired"


async def test_refresh_replaying_old_returns_401_revoked(
    client: httpx.AsyncClient,
) -> None:
    """rotation 后旧 refresh 应该被拉黑, 第二次复用拒绝."""
    body = await _login(client, phone="+8613800138006")
    refresh1 = body["tokens"]["refresh_token"]

    r2 = await _refresh(client, refresh1)
    assert r2.status_code == 200

    r_replay = await _refresh(client, refresh1)
    assert r_replay.status_code == 401
    assert r_replay.json()["detail"]["code"] == "token_revoked"


async def test_refresh_for_disabled_user_returns_401_unavailable(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = await _login(client, phone="+8613800138007")
    refresh = body["tokens"]["refresh_token"]

    async with session_factory() as session:
        await session.execute(
            update(User)
            .where(User.user_id == uuid.UUID(body["user"]["user_id"]))
            .values(status=0)
        )
        await session.commit()

    r = await _refresh(client, refresh)
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "user_unavailable"


# ------------------- /auth/logout -------------------


async def test_logout_revokes_access_and_refresh(client: httpx.AsyncClient) -> None:
    body = await _login(client, phone="+8613800138008")
    access = body["tokens"]["access_token"]
    refresh = body["tokens"]["refresh_token"]

    # logout 带 refresh body
    r = await client.post(
        "/api/v1/auth/logout",
        headers=_bearer(access),
        json={"refresh_token": refresh},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["logged_out"] is True
    assert out["revoked_access"] is True
    assert out["revoked_refresh"] is True

    # access 立刻不能用
    me = await client.get("/api/v1/me", headers=_bearer(access))
    assert me.status_code == 401
    assert me.json()["detail"]["code"] == "token_revoked"

    # refresh 也不能再换
    r2 = await _refresh(client, refresh)
    assert r2.status_code == 401
    assert r2.json()["detail"]["code"] == "token_revoked"


async def test_logout_without_refresh_body_only_revokes_access(
    client: httpx.AsyncClient,
) -> None:
    body = await _login(client, phone="+8613800138009")
    access = body["tokens"]["access_token"]
    refresh = body["tokens"]["refresh_token"]

    r = await client.post("/api/v1/auth/logout", headers=_bearer(access))
    assert r.status_code == 200
    out = r.json()
    assert out["revoked_access"] is True
    assert out["revoked_refresh"] is False

    # refresh 仍可用
    r2 = await _refresh(client, refresh)
    assert r2.status_code == 200


async def test_logout_without_authorization_returns_401(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post("/api/v1/auth/logout", json={"refresh_token": "x"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_missing"


async def test_logout_refuses_to_blacklist_other_users_refresh(
    client: httpx.AsyncClient,
) -> None:
    """user A logout 时 body 里塞 user B 的 refresh_token, 应当被拒绝拉黑 (returns False)."""
    body_a = await _login(client, phone="+8613800138010")
    body_b = await _login(client, phone="+8613800138011")

    r = await client.post(
        "/api/v1/auth/logout",
        headers=_bearer(body_a["tokens"]["access_token"]),
        json={"refresh_token": body_b["tokens"]["refresh_token"]},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["revoked_access"] is True
    assert out["revoked_refresh"] is False  # 关键: 拒绝拉黑别人的 refresh

    # B 的 refresh 仍然可用
    r2 = await _refresh(client, body_b["tokens"]["refresh_token"])
    assert r2.status_code == 200


# ------------------- 限流 -------------------


async def test_refresh_rate_limit_kicks_in_after_5_per_minute(
    client: httpx.AsyncClient,
) -> None:
    body = await _login(client, phone="+8613800138012")
    refresh = body["tokens"]["refresh_token"]

    # 第 1 次成功 + 拉黑旧 refresh, 第 2-5 次都用同一个旧 refresh ⇒ 401 token_revoked,
    # 但限流的 key 用的是 token 本身, 5 次内即便业务失败也会计数, 第 6 次该 429
    statuses = []
    for _ in range(6):
        r = await _refresh(client, refresh)
        statuses.append(r.status_code)
    assert statuses[0] == 200
    assert statuses[5] == 429, f"expected 429 on 6th call, got {statuses}"


# ------------------- blacklist 单元 -------------------


async def test_blacklist_unit_set_and_check(redis_client: InMemoryRedisClient) -> None:
    import time

    jti = "unit-jti-1"
    exp = int(time.time()) + 60
    ok = await blacklist_jti(jti, exp, reason="unit-test")
    assert ok is True
    assert await is_jti_blacklisted(jti) is True
    # 命名空间隔离
    assert await is_jti_blacklisted("other-jti") is False


async def test_blacklist_unit_skips_expired_token(
    redis_client: InMemoryRedisClient,  # noqa: ARG001
) -> None:
    import time

    jti = "unit-jti-expired"
    exp = int(time.time()) - 10
    ok = await blacklist_jti(jti, exp, reason="unit-test")
    assert ok is False  # 过期的 token 不需要拉黑
    assert await is_jti_blacklisted(jti) is False


async def test_blacklist_check_failopen(monkeypatch) -> None:
    """Redis 故障时, is_jti_blacklisted 必须 fail-open (避免单点故障导致全员 401)."""

    class _ExplodingRedis:
        async def get(self, key: str) -> str | None:
            raise RuntimeError("boom")

        async def set(self, *a, **kw) -> None:
            raise RuntimeError("boom")

    set_redis_client(_ExplodingRedis())
    try:
        assert await is_jti_blacklisted("any") is False
    finally:
        reset_redis_client()
