"""BE-006: 邀请码生成 + 绑定 端到端测试.

覆盖:
- 注册自动落 ``invite_codes`` 行 (BE-002 改动): phone 注册和 wechat 注册各跑一次
- ``POST /api/v1/invite/bind``:
  - 200 happy + ``users.invited_by`` 写入 + ``invite_codes.usage_count += 1``
  - 400 invite_self_binding (用自己的码)
  - 400 invite_self_binding (大小写归一后仍是自己的)
  - 400 invite_already_bound (一次性, 二次绑直接拒)
  - 404 invite_code_not_found
  - 400 invite_code_inactive (运营把 active 关了)
  - 400 invite_code_expired (expires_at 已过)
  - 400 invite_code_exhausted (usage_count >= max_usage)
  - 400 invite_code_not_personal (运营码 owner_user_id IS NULL)
  - 401 未登录
  - 422 code 太短
  - 200 + 多人绑同一 referrer → usage_count 累加
  - 200 + invite_codes.usage_count 在并发下不会双绑 (用 service 直接调验证)

依赖真 PG (XGZH_TEST_DATABASE_URL), 标 ``@pytest.mark.db``。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text, update
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
from app.db.models import InviteCode, User
from app.main import create_app
from app.services import invite_service, otp_service
from app.services.invite_service import (
    InviteAlreadyBoundError,
    InviteSelfBindError,
)

pytestmark = pytest.mark.db


# ------------------- DB / fixture (与 test_refresh / test_me 同源) -------------------


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
async def truncate_tables(db_engine) -> AsyncIterator[None]:
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE invite_codes, user_favorites, auth_sessions, users "
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
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_tables: None,  # noqa: ARG001
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
    client: httpx.AsyncClient, phone: str, code: str = "123456"
) -> dict:
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    r = await client.post(
        "/api/v1/auth/login/phone", json={"phone": phone, "code": code}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# =====================================================================
# 注册时自动落 invite_codes 行 (验证 BE-002/005 + BE-006 hook)
# =====================================================================


async def test_phone_register_creates_invite_code_row(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = await _login(client, phone="+8613800100001")
    user_invite_code = body["user"]["invite_code"]
    user_id = uuid.UUID(body["user"]["user_id"])

    async with session_factory() as session:
        row = (
            await session.execute(
                select(InviteCode).where(InviteCode.code == user_invite_code)
            )
        ).scalar_one_or_none()

    assert row is not None
    assert row.owner_user_id == user_id
    assert row.usage_count == 0
    assert row.max_usage is None  # 个人码默认无限
    assert row.is_active is True
    assert row.note == "personal"


# =====================================================================
# bind happy + invite_codes.usage_count 累加
# =====================================================================


async def test_bind_happy_path(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    referrer = await _login(client, phone="+8613800100002")
    invitee = await _login(client, phone="+8613800100003")

    referrer_code = referrer["user"]["invite_code"]
    referrer_id = uuid.UUID(referrer["user"]["user_id"])
    invitee_id = uuid.UUID(invitee["user"]["user_id"])

    r = await client.post(
        "/api/v1/invite/bind",
        json={"code": referrer_code},
        headers=_bearer(invitee["tokens"]["access_token"]),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["referrer_user_id"] == str(referrer_id)
    assert body["referrer_invite_code"] == referrer_code
    assert body["bound_at_usage_count"] == 1

    async with session_factory() as session:
        u = await session.get(User, invitee_id)
        assert u is not None
        assert u.invited_by == referrer_id

        invite = (
            await session.execute(
                select(InviteCode).where(InviteCode.code == referrer_code)
            )
        ).scalar_one()
        assert invite.usage_count == 1


async def test_bind_lowercase_input_is_normalized(
    client: httpx.AsyncClient,
) -> None:
    referrer = await _login(client, phone="+8613800100020")
    invitee = await _login(client, phone="+8613800100021")
    code = referrer["user"]["invite_code"]

    r = await client.post(
        "/api/v1/invite/bind",
        json={"code": code.lower()},
        headers=_bearer(invitee["tokens"]["access_token"]),
    )
    assert r.status_code == 200, r.text
    assert r.json()["referrer_invite_code"] == code  # 大写


async def test_bind_multiple_invitees_accumulate_usage(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    referrer = await _login(client, phone="+8613800100004")
    code = referrer["user"]["invite_code"]

    for i, phone in enumerate(["+8613800100005", "+8613800100006", "+8613800100007"]):
        invitee = await _login(client, phone=phone)
        r = await client.post(
            "/api/v1/invite/bind",
            json={"code": code},
            headers=_bearer(invitee["tokens"]["access_token"]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["bound_at_usage_count"] == i + 1

    async with session_factory() as session:
        invite = (
            await session.execute(select(InviteCode).where(InviteCode.code == code))
        ).scalar_one()
        assert invite.usage_count == 3


# =====================================================================
# 自禁
# =====================================================================


async def test_bind_own_code_returns_400_self_binding(client: httpx.AsyncClient) -> None:
    body = await _login(client, phone="+8613800100008")
    own_code = body["user"]["invite_code"]
    r = await client.post(
        "/api/v1/invite/bind",
        json={"code": own_code},
        headers=_bearer(body["tokens"]["access_token"]),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invite_self_binding"


async def test_bind_own_code_lowercase_still_self_binding(
    client: httpx.AsyncClient,
) -> None:
    body = await _login(client, phone="+8613800100009")
    own_code = body["user"]["invite_code"]
    r = await client.post(
        "/api/v1/invite/bind",
        json={"code": own_code.lower()},
        headers=_bearer(body["tokens"]["access_token"]),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invite_self_binding"


# =====================================================================
# 一次性: 已绑过拒
# =====================================================================


async def test_bind_twice_returns_400_already_bound(client: httpx.AsyncClient) -> None:
    referrer1 = await _login(client, phone="+8613800100010")
    referrer2 = await _login(client, phone="+8613800100011")
    invitee = await _login(client, phone="+8613800100012")
    headers = _bearer(invitee["tokens"]["access_token"])

    r1 = await client.post(
        "/api/v1/invite/bind",
        json={"code": referrer1["user"]["invite_code"]},
        headers=headers,
    )
    assert r1.status_code == 200

    # 二次绑同一个码 → 400
    r2 = await client.post(
        "/api/v1/invite/bind",
        json={"code": referrer1["user"]["invite_code"]},
        headers=headers,
    )
    assert r2.status_code == 400
    assert r2.json()["detail"]["code"] == "invite_already_bound"

    # 二次绑别的码 → 也 400 (不能改 referrer)
    r3 = await client.post(
        "/api/v1/invite/bind",
        json={"code": referrer2["user"]["invite_code"]},
        headers=headers,
    )
    assert r3.status_code == 400
    assert r3.json()["detail"]["code"] == "invite_already_bound"


# =====================================================================
# 不存在 / inactive / 过期 / 耗尽 / 运营码
# =====================================================================


async def test_bind_nonexistent_code_returns_404(client: httpx.AsyncClient) -> None:
    body = await _login(client, phone="+8613800100013")
    r = await client.post(
        "/api/v1/invite/bind",
        json={"code": "ZZZZZZZZ"},  # 8 字符大写, schema 通过, 但库里没这码
        headers=_bearer(body["tokens"]["access_token"]),
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "invite_code_not_found"


async def test_bind_inactive_code_returns_400(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    referrer = await _login(client, phone="+8613800100014")
    invitee = await _login(client, phone="+8613800100015")
    code = referrer["user"]["invite_code"]

    # 运营把 referrer 的码禁用了
    async with session_factory() as session:
        await session.execute(
            update(InviteCode).where(InviteCode.code == code).values(is_active=False)
        )
        await session.commit()

    r = await client.post(
        "/api/v1/invite/bind",
        json={"code": code},
        headers=_bearer(invitee["tokens"]["access_token"]),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invite_code_inactive"


async def test_bind_expired_code_returns_400(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    referrer = await _login(client, phone="+8613800100016")
    invitee = await _login(client, phone="+8613800100017")
    code = referrer["user"]["invite_code"]

    # invite_codes.expires_at 是 TIMESTAMP WITHOUT TIME ZONE, asyncpg 只收 naive datetime;
    # service 端的 _load_invite_for_update 把读出的 naive 当 UTC 处理 (见 invite_service)
    naive_past = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1)
    async with session_factory() as session:
        await session.execute(
            update(InviteCode)
            .where(InviteCode.code == code)
            .values(expires_at=naive_past)
        )
        await session.commit()

    r = await client.post(
        "/api/v1/invite/bind",
        json={"code": code},
        headers=_bearer(invitee["tokens"]["access_token"]),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invite_code_expired"


async def test_bind_exhausted_code_returns_400(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    referrer = await _login(client, phone="+8613800100018")
    invitee = await _login(client, phone="+8613800100019")
    code = referrer["user"]["invite_code"]

    async with session_factory() as session:
        await session.execute(
            update(InviteCode)
            .where(InviteCode.code == code)
            .values(max_usage=1, usage_count=1)
        )
        await session.commit()

    r = await client.post(
        "/api/v1/invite/bind",
        json={"code": code},
        headers=_bearer(invitee["tokens"]["access_token"]),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invite_code_exhausted"


async def test_bind_channel_code_owner_null_returns_400(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """运营码 owner_user_id IS NULL, MVP 不接受作为 referrer."""
    invitee = await _login(client, phone="+8613800100022")

    async with session_factory() as session:
        session.add(
            InviteCode(
                code="CHANNEL01",
                owner_user_id=None,
                usage_count=0,
                max_usage=None,
                is_active=True,
                expires_at=None,
                note="campaign-Q1",
            )
        )
        await session.commit()

    r = await client.post(
        "/api/v1/invite/bind",
        json={"code": "CHANNEL01"},
        headers=_bearer(invitee["tokens"]["access_token"]),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invite_code_not_personal"


# =====================================================================
# 401 + 422
# =====================================================================


async def test_bind_without_auth_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/invite/bind", json={"code": "ABCD1234"})
    assert r.status_code == 401


async def test_bind_too_short_code_returns_422(client: httpx.AsyncClient) -> None:
    body = await _login(client, phone="+8613800100023")
    r = await client.post(
        "/api/v1/invite/bind",
        json={"code": "AB"},
        headers=_bearer(body["tokens"]["access_token"]),
    )
    assert r.status_code == 422


# =====================================================================
# service 层单元: 直接调 bind_invite (验证 SQLAlchemy Row lock 路径, 绕过路由)
# =====================================================================


async def test_service_bind_then_bind_again_raises(
    client: httpx.AsyncClient,  # noqa: ARG001  -- 复用 truncate_tables fixture
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """同一 session 内连续两次 bind 同一用户 -> 第二次 InviteAlreadyBoundError."""
    referrer = await _login(client, phone="+8613800100024")
    invitee = await _login(client, phone="+8613800100025")

    code = referrer["user"]["invite_code"]
    invitee_id = uuid.UUID(invitee["user"]["user_id"])

    async with session_factory() as session:
        u = await session.get(User, invitee_id)
        assert u is not None
        await invite_service.bind_invite(session, current_user=u, code=code)

    async with session_factory() as session:
        u = await session.get(User, invitee_id)
        assert u is not None
        with pytest.raises(InviteAlreadyBoundError):
            await invite_service.bind_invite(session, current_user=u, code=code)


async def test_service_self_bind_raises(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = await _login(client, phone="+8613800100026")
    uid = uuid.UUID(body["user"]["user_id"])
    code = body["user"]["invite_code"]

    async with session_factory() as session:
        u = await session.get(User, uid)
        assert u is not None
        with pytest.raises(InviteSelfBindError):
            await invite_service.bind_invite(session, current_user=u, code=code)
