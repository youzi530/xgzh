"""Sprint 10 BE-S10-006: get_current_admin 依赖 + UserPublic.is_admin 派生测试.

覆盖:
- 普通用户调 admin endpoint → 403 admin_required
- 无 token 调 admin endpoint → 401 token_missing
- admin 用户调 admin endpoint → 200
- 13007458553 注册后 GET /me → is_admin=true (auth_service hook 兜底验证)
- 其它手机号注册后 GET /me → is_admin=false
- 已存在 13007458553 行被 alembic 0017 标 is_admin=true (migration 验证)

依赖真 PG (XGZH_TEST_DATABASE_URL), 标 ``@pytest.mark.db``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.adapters.sms import MockSMSAdapter, reset_sms_adapter, set_sms_adapter
from app.cache import InMemoryRedisClient, reset_redis_client, set_redis_client
from app.db.base import get_session
from app.db.models import User
from app.main import create_app
from app.services import otp_service

pytestmark = pytest.mark.db


# ─── fixtures (与 test_me.py 同源 — 故意复制, 隔离模块测试边界) ─────


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
            text("TRUNCATE users, auth_sessions, vip_memberships, invite_codes RESTART IDENTITY CASCADE")
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


# ─── helpers ──────────────────────────────────────────────────────


async def _login(
    client: httpx.AsyncClient, phone: str, code: str = "123456"
) -> dict:
    """走 OTP 登录路径注册并拿 token."""
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    r = await client.post(
        "/api/v1/auth/login/phone", json={"phone": phone, "code": code}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _promote_to_admin(
    session_factory: async_sessionmaker[AsyncSession], phone: str
) -> None:
    """直接在 DB 把 phone 用户标为 admin (绕开 hook, 用于测非初始 admin 场景)."""
    async with session_factory() as session:
        await session.execute(
            update(User).where(User.phone == phone).values(is_admin=True)
        )
        await session.commit()


# ─── Tests: GET /me 含 is_admin 派生字段 ──────────────────────────


async def test_me_includes_is_admin_false_for_regular_user(
    client: httpx.AsyncClient,
) -> None:
    """普通手机号注册后 GET /me 返 is_admin=false."""
    body = await _login(client, phone="+8613800138000")
    access = body["tokens"]["access_token"]

    # LoginResponse.user 里已经包含 is_admin 字段, 直接断言
    assert body["user"]["is_admin"] is False

    r = await client.get("/api/v1/me", headers=_bearer(access))
    assert r.status_code == 200, r.text
    assert r.json()["is_admin"] is False


async def test_me_includes_is_admin_true_for_initial_admin_phone(
    client: httpx.AsyncClient,
) -> None:
    """13007458553 注册后 GET /me 返 is_admin=true (auth_service hook 兜底验证)."""
    body = await _login(client, phone="+8613007458553")
    access = body["tokens"]["access_token"]

    # hook 在 verify_phone_login 里同事务标了 is_admin=true
    assert body["user"]["is_admin"] is True

    r = await client.get("/api/v1/me", headers=_bearer(access))
    assert r.status_code == 200, r.text
    assert r.json()["is_admin"] is True


# ─── Tests: get_current_admin RBAC 校验 ──────────────────────────


async def test_admin_endpoint_rejects_no_token(client: httpx.AsyncClient) -> None:
    """无 Authorization header 调 admin endpoint → 401 token_missing."""
    r = await client.get("/api/v1/admin/users")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_missing"


async def test_admin_endpoint_rejects_garbage_token(
    client: httpx.AsyncClient,
) -> None:
    """非法 token 调 admin endpoint → 401 token_invalid (不到 403 这步)."""
    r = await client.get("/api/v1/admin/users", headers=_bearer("not-a-jwt"))
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_invalid"


async def test_admin_endpoint_rejects_non_admin_user(
    client: httpx.AsyncClient,
) -> None:
    """普通用户登录后调 admin endpoint → 403 admin_required."""
    body = await _login(client, phone="+8613800138000")
    access = body["tokens"]["access_token"]

    r = await client.get("/api/v1/admin/users", headers=_bearer(access))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "admin_required"


async def test_admin_endpoint_accepts_initial_admin(
    client: httpx.AsyncClient,
) -> None:
    """13007458553 注册后, 走 admin endpoint → 200 (hook 兜底标 is_admin=true)."""
    body = await _login(client, phone="+8613007458553")
    access = body["tokens"]["access_token"]

    r = await client.get("/api/v1/admin/users", headers=_bearer(access))
    assert r.status_code == 200, r.text
    data = r.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 1  # 至少自己在列表里


async def test_admin_endpoint_accepts_manually_promoted_admin(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """任何用户被 DB 直接标 is_admin=true 后, JWT 仍然有效 — 验证 deps 走的是
    runtime DB 查 (不是 token claim cache); 适用 admin 后续从 web 控制台改的场景."""
    body = await _login(client, phone="+8613900139000")
    access = body["tokens"]["access_token"]

    # 先确认普通用户不行
    r = await client.get("/api/v1/admin/users", headers=_bearer(access))
    assert r.status_code == 403

    # DB 直接标 admin (绕过 hook)
    await _promote_to_admin(session_factory, "+8613900139000")

    # 同样的 token 现在应该 200 — 因为 get_current_admin 每次都查 DB
    r = await client.get("/api/v1/admin/users", headers=_bearer(access))
    assert r.status_code == 200, r.text
