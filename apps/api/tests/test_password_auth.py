"""BUG-S9-001 / BUG-S9-002 密码注册 / 登录 / 设置密码 端到端测试.

覆盖:
- 200 注册新用户 (phone) → ``is_new_user=True`` + ``has_password=True`` + ``profile_complete=True``
- 200 注册新用户 (email) → 同款
- 409 phone_already_exists
- 409 email_already_exists
- 422 / 400 password_format_invalid (无数字 / 太短)
- 422 / 400 identifier 全空 → at_least_one_credential
- 200 密码登录 (phone identifier) → 同 user_id
- 200 密码登录 (email identifier)
- 401 invalid_credentials (用户不存在; 防 enumeration)
- 401 invalid_credentials (密码错)
- 200 PUT /me/password 老 OTP 用户首次设密码 (has_password 由 false → true)
- 401 current_password_invalid 已有密码改密时旧密码错
- 200 已有密码改密 + current_password 正确
- 401 invalid_credentials 改完密码后旧密码再登录失败
- bcrypt hash 实际写库 (60 字符 / $2b$ 前缀)

依赖真 Postgres (``XGZH_TEST_DATABASE_URL``); 没配跳过.
复用 test_auth_login.py 同款 fixtures pattern (drop tables → upgrade head → 每用例 truncate).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.db.base import get_session
from app.db.models import User
from app.main import create_app

pytestmark = pytest.mark.db


# ---------- 复用 test_auth_login.py 同款 fixtures ----------


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
                await conn.execute(
                    text(f'DROP TABLE IF EXISTS public."{tbl}" CASCADE')
                )
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
            text(
                "TRUNCATE users, auth_sessions, user_favorites RESTART IDENTITY CASCADE"
            )
        )
    yield
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE users, auth_sessions, user_favorites RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
async def redis_client() -> AsyncIterator[InMemoryRedisClient]:
    client = InMemoryRedisClient()
    set_redis_client(client)
    yield client
    await client.aclose()
    reset_redis_client()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_users: None,  # noqa: ARG001
    redis_client: InMemoryRedisClient,  # noqa: ARG001
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


# ---------- helpers ----------


async def _register(
    client: httpx.AsyncClient,
    *,
    phone: str | None = None,
    email: str | None = None,
    password: str = "secret123",
    invite_code: str | None = None,
) -> httpx.Response:
    body: dict[str, str | None] = {"password": password}
    if phone is not None:
        body["phone"] = phone
    if email is not None:
        body["email"] = email
    if invite_code is not None:
        body["invite_code"] = invite_code
    return await client.post("/api/v1/auth/register/password", json=body)


async def _login_password(
    client: httpx.AsyncClient, identifier: str, password: str
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login/password",
        json={"identifier": identifier, "password": password},
    )


# ---------- 注册 200 happy ----------


async def test_register_with_phone_creates_user_with_password(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    r = await _register(client, phone="13800138000", password="abc12345")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["is_new_user"] is True
    assert body["user"]["has_phone"] is True
    assert body["user"]["has_email"] is False
    assert body["user"]["has_password"] is True
    assert body["user"]["profile_complete"] is True
    assert body["tokens"]["access_token"]
    assert body["tokens"]["refresh_token"]

    async with session_factory() as session:
        result = await session.execute(
            select(User).where(User.phone == "+8613800138000")
        )
        u = result.scalar_one()
        # bcrypt hash 形态: $2b$12$... 共 60 字符
        assert u.password_hash is not None
        assert u.password_hash.startswith("$2b$")
        assert len(u.password_hash) == 60


async def test_register_with_email_creates_user(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    r = await _register(client, email="alice@example.com", password="abc12345")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["user"]["has_phone"] is False
    assert body["user"]["has_email"] is True
    assert body["user"]["has_password"] is True

    async with session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == "alice@example.com")
        )
        u = result.scalar_one()
        assert u.email == "alice@example.com"
        assert u.password_hash is not None


async def test_register_normalizes_email_to_lowercase(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """邮箱 normalize: 大小写 → 小写 (避免重复账号)."""
    r = await _register(client, email="Bob@Example.COM", password="pass1234")
    assert r.status_code == 200, r.text
    async with session_factory() as session:
        result = await session.execute(select(User).where(User.email.isnot(None)))
        u = result.scalar_one()
        assert u.email == "bob@example.com"


# ---------- 注册 conflict ----------


async def test_register_phone_already_exists_returns_409(
    client: httpx.AsyncClient,
) -> None:
    r1 = await _register(client, phone="13800138000", password="abc12345")
    assert r1.status_code == 200

    r2 = await _register(client, phone="13800138000", password="other999")
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "phone_already_exists"


async def test_register_email_already_exists_returns_409(
    client: httpx.AsyncClient,
) -> None:
    r1 = await _register(client, email="dup@example.com", password="abc12345")
    assert r1.status_code == 200

    r2 = await _register(client, email="DUP@example.com", password="other999")
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "email_already_exists"


# ---------- 注册 422 校验 ----------


async def test_register_password_without_digit_returns_422(
    client: httpx.AsyncClient,
) -> None:
    r = await _register(client, phone="13800138000", password="abcdefgh")
    assert r.status_code == 422


async def test_register_password_too_short_returns_422(
    client: httpx.AsyncClient,
) -> None:
    r = await _register(client, phone="13800138000", password="ab1")
    assert r.status_code == 422


async def test_register_no_phone_no_email_returns_422(
    client: httpx.AsyncClient,
) -> None:
    r = await _register(client, password="abc12345")
    assert r.status_code == 422


# ---------- 密码登录 200 happy ----------


async def test_login_with_phone_identifier(client: httpx.AsyncClient) -> None:
    r1 = await _register(client, phone="13800138000", password="abc12345")
    user_id_1 = r1.json()["user"]["user_id"]

    # 用 不带 +86 的 phone 登录, 后端归一化后应能命中
    r2 = await _login_password(client, "13800138000", "abc12345")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["is_new_user"] is False
    assert body["user"]["user_id"] == user_id_1


async def test_login_with_email_identifier_case_insensitive(
    client: httpx.AsyncClient,
) -> None:
    r1 = await _register(client, email="alice@example.com", password="abc12345")
    user_id_1 = r1.json()["user"]["user_id"]

    # 大小写不应影响登录
    r2 = await _login_password(client, "Alice@Example.com", "abc12345")
    assert r2.status_code == 200
    assert r2.json()["user"]["user_id"] == user_id_1


# ---------- 密码登录 401 ----------


async def test_login_unknown_identifier_returns_401_invalid_credentials(
    client: httpx.AsyncClient,
) -> None:
    """防 enumeration: 用户不存在时不应暴露, 统一 invalid_credentials."""
    r = await _login_password(client, "13900139000", "anypass1")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "invalid_credentials"


async def test_login_wrong_password_returns_401(client: httpx.AsyncClient) -> None:
    await _register(client, phone="13800138000", password="abc12345")
    r = await _login_password(client, "13800138000", "wrong1234")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "invalid_credentials"


# ---------- PUT /me/password 设置 / 修改密码 ----------


async def test_set_password_first_time_for_otp_user(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """模拟老 OTP 用户: 直接 INSERT 一个 has_password=false 的 User, 再用 PUT /me/password 设密码."""
    # Step 1: 用密码注册一个 user (拿到 token), 然后手动把 password_hash 清掉模拟"老 OTP 用户"
    r = await _register(client, phone="13800138000", password="initial1")
    assert r.status_code == 200
    initial_token = r.json()["tokens"]["access_token"]
    user_id = r.json()["user"]["user_id"]

    async with session_factory() as session:
        u = await session.get(User, user_id)
        assert u is not None
        u.password_hash = None
        await session.commit()

    # Step 2: 用现有 token 调 PUT /me/password (current_password 不传)
    r2 = await client.put(
        "/api/v1/me/password",
        json={"password": "newpass1"},
        headers={"Authorization": f"Bearer {initial_token}"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["has_password"] is True

    # Step 3: 旧密码登不上 (因为已经清了再设新)
    r3 = await _login_password(client, "13800138000", "initial1")
    assert r3.status_code == 401

    # Step 4: 新密码登得上
    r4 = await _login_password(client, "13800138000", "newpass1")
    assert r4.status_code == 200


async def test_change_password_requires_current_password(
    client: httpx.AsyncClient,
) -> None:
    """已有密码用户, 不传 current_password → 401 (防 session 劫持后改密)."""
    r = await _register(client, phone="13800138000", password="initial1")
    token = r.json()["tokens"]["access_token"]

    r2 = await client.put(
        "/api/v1/me/password",
        json={"password": "newpass1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    # 后端期望 current_password 必填; 不填时按 current_password_invalid 处理
    assert r2.status_code == 401
    assert r2.json()["detail"]["code"] == "current_password_invalid"


async def test_change_password_with_wrong_current_returns_401(
    client: httpx.AsyncClient,
) -> None:
    r = await _register(client, phone="13800138000", password="initial1")
    token = r.json()["tokens"]["access_token"]

    r2 = await client.put(
        "/api/v1/me/password",
        json={"password": "newpass1", "current_password": "wrong999"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 401
    assert r2.json()["detail"]["code"] == "current_password_invalid"


async def test_change_password_with_correct_current_succeeds(
    client: httpx.AsyncClient,
) -> None:
    r = await _register(client, phone="13800138000", password="initial1")
    token = r.json()["tokens"]["access_token"]

    r2 = await client.put(
        "/api/v1/me/password",
        json={"password": "newpass1", "current_password": "initial1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 200, r2.text

    # 旧密码不再生效
    r3 = await _login_password(client, "13800138000", "initial1")
    assert r3.status_code == 401

    # 新密码生效
    r4 = await _login_password(client, "13800138000", "newpass1")
    assert r4.status_code == 200


# ---------- UserPublic 派生字段 ----------


async def test_userpublic_has_flags_for_password_user(
    client: httpx.AsyncClient,
) -> None:
    r = await _register(client, phone="13800138000", password="abc12345")
    user = r.json()["user"]
    assert user["has_phone"] is True
    assert user["has_email"] is False
    assert user["has_password"] is True
    assert user["has_wechat"] is False
    assert user["profile_complete"] is True


async def test_userpublic_has_flags_for_email_only_user(
    client: httpx.AsyncClient,
) -> None:
    r = await _register(client, email="solo@example.com", password="abc12345")
    user = r.json()["user"]
    assert user["has_phone"] is False
    assert user["has_email"] is True
    assert user["has_password"] is True
    assert user["profile_complete"] is True
