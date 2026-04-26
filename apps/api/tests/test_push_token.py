"""BE-011: 推送 token 注册 / 注销 API 端到端测试.

覆盖:

A. 鉴权 (BE-003 deps 路径)
   1. POST /push/tokens 未登录 → 401 ``token_missing``
   2. DELETE /push/tokens 未登录 → 401

B. POST /push/tokens (注册)
   1. 首次注册 → 200 + ``created=True`` + 不回显 token
   2. 同 user+platform+device_id 复发 → 200 + ``created=False`` + token 被覆盖
      (DB 直查证实)
   3. 同 user+platform 但不同 device_id → 两条独立行 (created=True 各一次)
   4. 同 user+device_id 但不同 platform → 两条独立行 (跨平台多设备同步)
   5. ``platform`` 不在白名单 → 422
   6. ``device_id`` 缺失 → 422
   7. ``token`` 长度不足 → 422

C. DELETE /push/tokens?platform=&device_id=
   1. 删已注册 → 200 + ``removed=True`` + DB 该行真没了
   2. 重复删 → 200 + ``removed=False`` (幂等)
   3. 用户隔离: A 注册 → B 删同 platform+device_id → ``removed=False``,
      A 那条仍在
   4. 缺 query 参数 → 422
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

from app.adapters.sms import MockSMSAdapter, reset_sms_adapter, set_sms_adapter
from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.db.base import get_engine, get_session
from app.db.base import get_session_factory as _get_factory_lru
from app.db.models import PushToken
from app.main import create_app
from app.services import otp_service

pytestmark = pytest.mark.db


# ─── Alembic schema (与其它 db 测试同源) ─────────────────────────


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
async def truncate_all(db_engine) -> AsyncIterator[None]:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE users, auth_sessions, push_tokens, invite_codes "
                "RESTART IDENTITY CASCADE"
            )
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
async def patch_session_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    _get_factory_lru.cache_clear()
    get_engine.cache_clear()

    import app.db as db_pkg

    orig_pkg = db_pkg.get_session_factory
    db_pkg.get_session_factory = lambda: session_factory  # type: ignore[assignment]
    try:
        yield
    finally:
        db_pkg.get_session_factory = orig_pkg
        _get_factory_lru.cache_clear()
        get_engine.cache_clear()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    redis_client: InMemoryRedisClient,  # noqa: ARG001
    mock_sms: MockSMSAdapter,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
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


# ─── helpers ─────────────────────────────────────────


async def _login(
    cli: httpx.AsyncClient, phone: str = "+8613800138000", code: str = "123456"
) -> dict:
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    r = await cli.post("/api/v1/auth/login/phone", json={"phone": phone, "code": code})
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


def _payload(
    *,
    platform: str = "ios",
    token: str = "a" * 64,
    device_id: str = "device-iphone-15-pro",
) -> dict:
    return {"platform": platform, "token": token, "device_id": device_id}


# ═══════════ A. 鉴权 ═══════════


async def test_post_without_token_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/push/tokens", json=_payload())
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_missing"


async def test_delete_without_token_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.delete("/api/v1/push/tokens?platform=ios&device_id=x")
    assert r.status_code == 401


# ═══════════ B. POST /push/tokens ═══════════


async def test_register_first_time_created_true_and_token_not_echoed(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r = await client.post("/api/v1/push/tokens", json=_payload(), headers=h)
    assert r.status_code == 200, r.text
    j = r.json()

    assert j["ok"] is True
    assert j["created"] is True
    assert j["platform"] == "ios"
    assert j["device_id"] == "device-iphone-15-pro"
    assert j["is_active"] is True
    assert isinstance(j["id"], int) and j["id"] > 0
    assert "registered_at" in j
    # 安全: 响应里绝不能有 token 内容
    assert "token" not in j, "响应不该泄露推送 token (敏感凭据)"

    # DB 真存了
    async with session_factory() as s:
        rows = (await s.execute(select(PushToken))).scalars().all()
        assert len(rows) == 1
        assert rows[0].token == "a" * 64
        assert rows[0].is_active is True


async def test_register_idempotent_overwrites_token(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r1 = await client.post("/api/v1/push/tokens", json=_payload(token="a" * 64), headers=h)
    assert r1.json()["created"] is True
    id1 = r1.json()["id"]

    # APNs token 轮换是常见场景: 同 user+platform+device 重新注册新 token
    r2 = await client.post("/api/v1/push/tokens", json=_payload(token="b" * 64), headers=h)
    assert r2.status_code == 200
    assert r2.json()["created"] is False, "同 device 复发必须幂等"
    assert r2.json()["id"] == id1, "应保留同一行, 不能新增"

    # DB 里 token 真被覆盖了 (验证不是 echo 的假覆盖)
    async with session_factory() as s:
        rows = (await s.execute(select(PushToken))).scalars().all()
        assert len(rows) == 1, "ON CONFLICT 必须 UPDATE, 不能 INSERT"
        assert rows[0].token == "b" * 64


async def test_register_different_devices_create_separate_rows(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一用户多台 iOS 设备 (例如 iPhone + iPad), 每台独立一行."""
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r1 = await client.post(
        "/api/v1/push/tokens",
        json=_payload(device_id="iphone-15"),
        headers=h,
    )
    r2 = await client.post(
        "/api/v1/push/tokens",
        json=_payload(device_id="ipad-pro"),
        headers=h,
    )
    assert r1.json()["created"] is True
    assert r2.json()["created"] is True
    assert r1.json()["id"] != r2.json()["id"]

    async with session_factory() as s:
        rows = (await s.execute(select(PushToken))).scalars().all()
        assert len(rows) == 2


async def test_register_different_platforms_same_device_id_create_separate_rows(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同 device_id 但不同 platform: PWA + 微信小程序 共用同一台 iPhone 也算两行."""
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    await client.post(
        "/api/v1/push/tokens",
        json=_payload(platform="ios", device_id="my-phone"),
        headers=h,
    )
    await client.post(
        "/api/v1/push/tokens",
        json=_payload(platform="wxmp", token="o" * 32, device_id="my-phone"),
        headers=h,
    )

    async with session_factory() as s:
        rows = (await s.execute(select(PushToken))).scalars().all()
        assert len(rows) == 2
        platforms = sorted(r.platform for r in rows)
        assert platforms == ["ios", "wxmp"]


async def test_register_unknown_platform_returns_422(
    client: httpx.AsyncClient,
) -> None:
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r = await client.post(
        "/api/v1/push/tokens",
        json=_payload(platform="windows-phone"),
        headers=h,
    )
    assert r.status_code == 422


async def test_register_missing_device_id_returns_422(
    client: httpx.AsyncClient,
) -> None:
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r = await client.post(
        "/api/v1/push/tokens",
        json={"platform": "ios", "token": "a" * 64},
        headers=h,
    )
    assert r.status_code == 422


async def test_register_token_too_short_returns_422(
    client: httpx.AsyncClient,
) -> None:
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r = await client.post("/api/v1/push/tokens", json=_payload(token="abc"), headers=h)
    assert r.status_code == 422


# ═══════════ C. DELETE /push/tokens ═══════════


async def test_delete_happy_then_idempotent(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    await client.post("/api/v1/push/tokens", json=_payload(), headers=h)

    r1 = await client.delete(
        "/api/v1/push/tokens",
        params={"platform": "ios", "device_id": "device-iphone-15-pro"},
        headers=h,
    )
    assert r1.status_code == 200
    j1 = r1.json()
    assert j1 == {
        "ok": True,
        "platform": "ios",
        "device_id": "device-iphone-15-pro",
        "removed": True,
    }

    # 真删了
    async with session_factory() as s:
        rows = (await s.execute(select(PushToken))).scalars().all()
        assert rows == []

    # 幂等: 再删一次还是 200, removed=False
    r2 = await client.delete(
        "/api/v1/push/tokens",
        params={"platform": "ios", "device_id": "device-iphone-15-pro"},
        headers=h,
    )
    assert r2.status_code == 200
    assert r2.json()["removed"] is False


async def test_delete_isolated_per_user(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """B 用户用同样的 platform+device_id 也只能删自己的, 不会影响 A."""
    body_a = await _login(client, phone="+8613800138000")
    h_a = _bearer(body_a["tokens"]["access_token"])
    await client.post("/api/v1/push/tokens", json=_payload(), headers=h_a)

    body_b = await _login(client, phone="+8613900139000")
    h_b = _bearer(body_b["tokens"]["access_token"])

    r = await client.delete(
        "/api/v1/push/tokens",
        params={"platform": "ios", "device_id": "device-iphone-15-pro"},
        headers=h_b,
    )
    assert r.status_code == 200
    assert r.json()["removed"] is False, "B 用户没有这条记录, 不该删到 A 的"

    async with session_factory() as s:
        rows = (await s.execute(select(PushToken))).scalars().all()
        assert len(rows) == 1, "A 那条必须还在"


async def test_delete_missing_query_params_returns_422(
    client: httpx.AsyncClient,
) -> None:
    body = await _login(client)
    h = _bearer(body["tokens"]["access_token"])

    r = await client.delete("/api/v1/push/tokens", headers=h)
    assert r.status_code == 422
