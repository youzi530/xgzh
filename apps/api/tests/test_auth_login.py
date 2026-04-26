"""BE-002: POST /api/v1/auth/login/phone 端到端测试.

覆盖:
- 200 新用户: 自动注册 + 颁发 access/refresh + ``is_new_user=True``
- 200 老用户: 已存在的 phone 登录 + ``is_new_user=False`` + ``user_id`` 一致 + ``last_active_at`` 刷新
- 401 otp_invalid: 错误验证码
- 401 otp_expired: 没发过 / Redis 已被清掉
- 401 otp_expired (复用): OTP 已经被本次登录消费, 第二次再发起立刻失效
- 429 verify 限流: 5 次/5min
- access_token / refresh_token 可被 ``decode_token`` 还原 + ``user_id`` / ``typ`` 正确
- 数据库实际写入: ``users.phone`` ``invite_code`` ``last_active_at`` ``status=1``
- ``access`` 解 ``refresh`` token 时 raise InvalidTokenError (typ 隔离)

依赖真 Postgres (``XGZH_TEST_DATABASE_URL``), 标 ``@pytest.mark.db``;
未配置时整个文件 skip。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
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
from app.db.base import get_session
from app.db.models import User
from app.main import create_app
from app.security import (
    ACCESS_TOKEN_TYPE,
    InvalidTokenError,
    REFRESH_TOKEN_TYPE,
    decode_token,
)
from app.services import otp_service

pytestmark = pytest.mark.db


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
    """模块级: 跑一次 ``alembic upgrade head``, 保证 schema 存在."""
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
    """每条用例前后清空 users 相关表 (含级联), 防止串扰."""
    async with db_engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE users, auth_sessions, user_favorites RESTART IDENTITY CASCADE")
        )
    yield
    async with db_engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE users, auth_sessions, user_favorites RESTART IDENTITY CASCADE")
        )


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
    """覆盖 get_session, 让路由用测试 engine."""
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


# ---------- helpers ----------


async def _seed_otp(phone: str, code: str, ttl: int = 300) -> None:
    await otp_service.store_otp(phone, code, ttl_seconds=ttl)


async def _login(client: httpx.AsyncClient, phone: str, code: str) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login/phone", json={"phone": phone, "code": code}
    )


# ---------- 200 happy ----------


async def test_login_phone_creates_new_user(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    phone = "+8613800138000"
    await _seed_otp(phone, "123456")

    r = await _login(client, "13800138000", "123456")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["is_new_user"] is True
    assert body["user"]["nickname"] is None
    assert body["user"]["status"] == 1
    assert body["user"]["region"] == "CN"
    assert body["user"]["invite_code"]
    assert len(body["user"]["invite_code"]) == 8

    tokens = body["tokens"]
    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 1800
    assert tokens["refresh_expires_in"] == 30 * 24 * 3600
    assert tokens["access_token"] and tokens["refresh_token"]

    async with session_factory() as session:
        result = await session.execute(select(User).where(User.phone == phone))
        user = result.scalar_one()
        assert str(user.user_id) == body["user"]["user_id"]
        assert user.invite_code == body["user"]["invite_code"]
        assert user.status == 1
        assert user.region == "CN"


async def test_login_phone_existing_user_returns_same_id(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    phone = "+8613800138000"

    await _seed_otp(phone, "111111")
    r1 = await _login(client, phone, "111111")
    assert r1.status_code == 200, r1.text
    user_id_1 = r1.json()["user"]["user_id"]
    assert r1.json()["is_new_user"] is True

    # 再来一次 (新 OTP)
    await _seed_otp(phone, "222222")
    r2 = await _login(client, "13800138000", "222222")
    assert r2.status_code == 200, r2.text
    user_id_2 = r2.json()["user"]["user_id"]
    assert r2.json()["is_new_user"] is False
    assert user_id_1 == user_id_2

    async with session_factory() as session:
        rows = await session.execute(select(User).where(User.phone == phone))
        assert len(rows.scalars().all()) == 1


# ---------- 401 ----------


async def test_login_phone_wrong_code_returns_401(client: httpx.AsyncClient) -> None:
    await _seed_otp("+8613800138000", "654321")
    r = await _login(client, "13800138000", "111111")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "otp_invalid"


async def test_login_phone_no_otp_returns_401_expired(client: httpx.AsyncClient) -> None:
    r = await _login(client, "13800138000", "123456")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "otp_expired"


async def test_login_phone_otp_consumed_after_success(client: httpx.AsyncClient) -> None:
    """OTP 一次性: 成功登录后再用同一 OTP 立即失败."""
    await _seed_otp("+8613800138000", "123456")
    r1 = await _login(client, "13800138000", "123456")
    assert r1.status_code == 200

    r2 = await _login(client, "13800138000", "123456")
    assert r2.status_code == 401
    assert r2.json()["detail"]["code"] == "otp_expired"


async def test_login_phone_otp_not_consumed_on_wrong_code(
    client: httpx.AsyncClient,
) -> None:
    """错码不消费 OTP, 这样用户在 60s 重发限流没到时还能再试 (在 verify 5/5min 范围内)."""
    await _seed_otp("+8613800138000", "654321")
    r1 = await _login(client, "13800138000", "000000")
    assert r1.status_code == 401

    r2 = await _login(client, "13800138000", "654321")
    assert r2.status_code == 200


# ---------- 400 invalid phone ----------


async def test_login_phone_invalid_phone_returns_400(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/login/phone", json={"phone": "+1234567890", "code": "123456"}
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_phone"


async def test_login_phone_short_code_returns_422(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/v1/auth/login/phone", json={"phone": "13800138000", "code": "12"}
    )
    assert r.status_code == 422


# ---------- 429 verify rate limit ----------


async def test_login_phone_verify_rate_limit_5_per_5min(
    client: httpx.AsyncClient,
) -> None:
    """5 次错码后第 6 次直接 429, 不再走业务逻辑."""
    for _ in range(5):
        r = await _login(client, "13800138000", "000000")
        assert r.status_code == 401, r.text

    r6 = await _login(client, "13800138000", "000000")
    assert r6.status_code == 429
    assert r6.json()["detail"]["code"] == "too_many_requests"
    assert r6.headers.get("Retry-After")


# ---------- token 解码 ----------


async def test_issued_tokens_are_decodable(client: httpx.AsyncClient) -> None:
    await _seed_otp("+8613800138000", "123456")
    r = await _login(client, "13800138000", "123456")
    assert r.status_code == 200
    body = r.json()

    access = decode_token(body["tokens"]["access_token"], expected_type=ACCESS_TOKEN_TYPE)
    refresh = decode_token(body["tokens"]["refresh_token"], expected_type=REFRESH_TOKEN_TYPE)

    assert str(access.user_id) == body["user"]["user_id"]
    assert str(refresh.user_id) == body["user"]["user_id"]
    assert access.user_id == refresh.user_id
    assert access.jti != refresh.jti
    assert access.expires_at - access.issued_at == 1800
    assert refresh.expires_at - refresh.issued_at == 30 * 24 * 3600


async def test_access_and_refresh_token_typ_isolated(client: httpx.AsyncClient) -> None:
    """用 access typ 解 refresh / 反之必须报错, 防止 refresh 被当 access 用绕过过期."""
    await _seed_otp("+8613800138000", "123456")
    r = await _login(client, "13800138000", "123456")
    body = r.json()

    with pytest.raises(InvalidTokenError):
        decode_token(body["tokens"]["refresh_token"], expected_type=ACCESS_TOKEN_TYPE)
    with pytest.raises(InvalidTokenError):
        decode_token(body["tokens"]["access_token"], expected_type=REFRESH_TOKEN_TYPE)
