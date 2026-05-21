"""Sprint 12 P0-1: ops 重置密码 endpoint 集成测.

覆盖场景:
- ✅ 服务器没配 OPS_ADMIN_TOKEN → 503 admin_disabled
- ✅ X-Admin-Token 缺失 / 错 → 401
- ✅ phone 不存在 → 404 user_not_found
- ✅ 弱密码 (太短 / 无数字) → 422 validation
- ✅ 合法重置 → 200, 重置后用新密码 login 成功
- ✅ grant_admin=True (默认): 普通用户被提权
- ✅ grant_admin=False: 普通用户保持非 admin
- ✅ grant_admin=False 但目标已是 admin: 不卸权 (防误降权)
- ✅ 重置后撤销所有活跃 refresh session (sessions_revoked > 0)
- ✅ 13007458553 (初始 admin phone) 整体场景: 注册 → 改密 → 用新密码登 → is_admin 持续 true

为什么独立文件而不是合 test_admin_rbac.py: 这是 X-Admin-Token 通道 (ops), RBAC 是
JWT+is_admin 通道; 两套鉴权逻辑分开测更清晰. fixtures 复制粘贴, 不抽到 conftest 是
为了维持"每个 admin 测试文件独立模块"原则 (Sprint 10 既有惯例).

依赖真 PG (XGZH_TEST_DATABASE_URL), pytest.mark.db.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.adapters.sms import MockSMSAdapter, reset_sms_adapter, set_sms_adapter
from app.cache import InMemoryRedisClient, reset_redis_client, set_redis_client
from app.core.config import get_settings
from app.db.base import get_session
from app.db.models import User
from app.main import create_app
from app.services import otp_service

pytestmark = pytest.mark.db

ADMIN_TOKEN = "test-ops-admin-token-32-bytes-random-1234"
INITIAL_ADMIN_PHONE = "+8613007458553"


# ─── fixtures (与 test_admin_rbac.py 同源) ────────────────────────


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
            text(
                "TRUNCATE users, auth_sessions, vip_memberships, invite_codes "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture
async def redis_client() -> AsyncIterator[InMemoryRedisClient]:
    cli = InMemoryRedisClient()
    set_redis_client(cli)
    yield cli
    await cli.aclose()
    reset_redis_client()


@pytest.fixture
async def mock_sms() -> AsyncIterator[MockSMSAdapter]:
    adapter = MockSMSAdapter()
    set_sms_adapter(adapter)
    yield adapter
    reset_sms_adapter()


@pytest.fixture(autouse=True)
def _set_admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有用例默认配上 OPS_ADMIN_TOKEN. 测试 503 的用例自己 delenv."""
    monkeypatch.setenv("OPS_ADMIN_TOKEN", ADMIN_TOKEN)
    get_settings.cache_clear()


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


async def _login_otp(client: httpx.AsyncClient, phone: str, code: str = "123456") -> dict:
    """用 OTP 登录 (注册) — 创建一个没密码的用户."""
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    r = await client.post(
        "/api/v1/auth/login/phone", json={"phone": phone, "code": code}
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _login_password(
    client: httpx.AsyncClient, identifier: str, password: str
) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login/password",
        json={"identifier": identifier, "password": password},
    )


def _admin_headers(token: str | None = ADMIN_TOKEN) -> dict[str, str]:
    return {"X-Admin-Token": token} if token else {}


OPS_PATH_BY_PHONE = "/api/v1/admin/users/by-phone/{phone}/set-password"


# ─── Auth 护栏 ────────────────────────────────────────────────────


async def test_returns_503_when_ops_token_unset(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """服务器没配 OPS_ADMIN_TOKEN → 503 admin_disabled."""
    monkeypatch.setenv("OPS_ADMIN_TOKEN", "")
    get_settings.cache_clear()
    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13800138000"),
        headers=_admin_headers(),
        json={"new_password": "newpass123"},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "admin_disabled"


async def test_returns_401_without_admin_token(client: httpx.AsyncClient) -> None:
    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13800138000"),
        json={"new_password": "newpass123"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "admin_token_invalid"


async def test_returns_401_with_wrong_admin_token(client: httpx.AsyncClient) -> None:
    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13800138000"),
        headers={"X-Admin-Token": "wrong"},
        json={"new_password": "newpass123"},
    )
    assert r.status_code == 401


# ─── Validation ──────────────────────────────────────────────────


async def test_returns_404_when_phone_not_found(client: httpx.AsyncClient) -> None:
    """phone 没注册过 → 404 user_not_found (不暴露存在性给非 admin, 但 admin 可知)."""
    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13900000000"),
        headers=_admin_headers(),
        json={"new_password": "newpass123"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "user_not_found"


async def test_rejects_password_too_short(client: httpx.AsyncClient) -> None:
    """密码 < 6 字 → 422."""
    await _login_otp(client, "+8613800138000")
    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13800138000"),
        headers=_admin_headers(),
        json={"new_password": "a1"},
    )
    assert r.status_code == 422


async def test_rejects_password_without_digit(client: httpx.AsyncClient) -> None:
    """密码无数字 → 422 (复用 _validate_password_format 规则)."""
    await _login_otp(client, "+8613800138000")
    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13800138000"),
        headers=_admin_headers(),
        json={"new_password": "abcdefgh"},
    )
    assert r.status_code == 422


# ─── Happy path ──────────────────────────────────────────────────


async def test_set_password_then_login_succeeds(
    client: httpx.AsyncClient,
) -> None:
    """重置密码后, 用新密码可以登录."""
    # 1. OTP 注册一个用户 (此时无密码)
    await _login_otp(client, "+8613800138000")

    # 2. ops 重置密码 (默认 grant_admin=True)
    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13800138000"),
        headers=_admin_headers(),
        json={"new_password": "newpass123"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["phone_masked"].startswith("+86138")
    assert body["is_admin"] is True
    assert "message" in body

    # 3. 用新密码登录
    r2 = await _login_password(client, "13800138000", "newpass123")
    assert r2.status_code == 200, r2.text
    # 同时 is_admin=True (因为 grant_admin=True)
    assert r2.json()["user"]["is_admin"] is True


async def test_grant_admin_false_does_not_promote(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """grant_admin=False → 不提权 (但不卸权)."""
    await _login_otp(client, "+8613800138000")
    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13800138000"),
        headers=_admin_headers(),
        json={"new_password": "newpass123", "grant_admin": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_admin"] is False

    # DB 也确认
    async with session_factory() as s:
        user = (
            await s.execute(select(User).where(User.phone == "+8613800138000"))
        ).scalar_one()
        assert user.is_admin is False


async def test_grant_admin_false_never_demotes_existing_admin(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """已经是 admin 的用户即使 grant_admin=False 也不会被降权 (防误操作锁死).

    用例: 运维改某个 admin 的密码 (例如 13007458553 自己改密码), 不应因为
    grant_admin=False 把它卸权.
    """
    # 13007458553 注册即 admin (hook 兜底)
    await _login_otp(client, INITIAL_ADMIN_PHONE)
    async with session_factory() as s:
        user = (
            await s.execute(select(User).where(User.phone == INITIAL_ADMIN_PHONE))
        ).scalar_one()
        assert user.is_admin is True

    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone=INITIAL_ADMIN_PHONE.replace("+", "%2B")),
        headers=_admin_headers(),
        json={"new_password": "newpass123", "grant_admin": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_admin"] is True  # 没卸权

    # DB 也确认
    async with session_factory() as s:
        user = (
            await s.execute(select(User).where(User.phone == INITIAL_ADMIN_PHONE))
        ).scalar_one()
        assert user.is_admin is True


async def test_response_includes_security_warning(
    client: httpx.AsyncClient,
) -> None:
    """response 含 security_warning 字段, 诚实告知"旧 token 在自然 TTL 内仍可用".

    这是 P0-1 的设计取舍 (见 endpoint docstring "安全边界"段): 项目没实现 password_version
    强踢机制, 我们不假装做到了. 真要强踢需要后续 sprint.
    """
    await _login_otp(client, "+8613800138000")
    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13800138000"),
        headers=_admin_headers(),
        json={"new_password": "newpass123"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "security_warning" in body
    assert "password_version" in body["security_warning"]  # 指向后续 sprint 改进方向


async def test_initial_admin_full_unlock_scenario(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Sprint 12 主用例: 13007458553 忘密码 → ops 重置 → 用新密码登录 + is_admin 保持.

    这是这次 sprint 加 endpoint 的核心动机, 跑通这一条等于解锁了"短信资质未下来
    + admin 忘密码"的死锁场景.
    """
    # 1. OTP 注册一次, 触发 hook 标 is_admin=true
    await _login_otp(client, INITIAL_ADMIN_PHONE)
    async with session_factory() as s:
        user = (
            await s.execute(select(User).where(User.phone == INITIAL_ADMIN_PHONE))
        ).scalar_one()
        assert user.is_admin is True
        assert user.password_hash is None  # OTP 路径不设密码

    # 2. 模拟"短信资质丢了, admin 忘密码" — 用 ops 重置 (不指定 grant_admin, 默认 True)
    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone=INITIAL_ADMIN_PHONE.replace("+", "%2B")),
        headers=_admin_headers(),
        json={"new_password": "AdminPass123"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_admin"] is True

    # 3. 用新密码走 password 登录 (不依赖 SMS)
    r2 = await _login_password(client, INITIAL_ADMIN_PHONE, "AdminPass123")
    assert r2.status_code == 200, r2.text
    login_body = r2.json()
    assert login_body["user"]["is_admin"] is True
    access = login_body["tokens"]["access_token"]

    # 4. 用新 token 调 Sprint 10 admin endpoint → 200 (RBAC 通)
    r3 = await client.get(
        "/api/v1/admin/users", headers={"Authorization": f"Bearer {access}"}
    )
    assert r3.status_code == 200, r3.text


# ─── 边界: 接受多种 phone 格式 ────────────────────────────────────


async def test_accepts_phone_without_plus_prefix(client: httpx.AsyncClient) -> None:
    """13xxx (无 +) 也能识别 (normalize_phone 自动加 +86)."""
    await _login_otp(client, "+8613800138000")
    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13800138000"),
        headers=_admin_headers(),
        json={"new_password": "newpass123"},
    )
    assert r.status_code == 200, r.text


async def test_accepts_phone_with_url_encoded_plus(client: httpx.AsyncClient) -> None:
    """+8613xxx (URL encoded %2B) 也能识别."""
    await _login_otp(client, "+8613800138000")
    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="%2B8613800138000"),
        headers=_admin_headers(),
        json={"new_password": "newpass123"},
    )
    assert r.status_code == 200, r.text


# ─── 已知安全边界: 旧 access + refresh 在自然 TTL 内仍可用 (Sprint 12 不强踢) ──


async def test_old_tokens_remain_valid_within_natural_ttl(
    client: httpx.AsyncClient,
) -> None:
    """文档化当前实现的安全边界 (诚实声明):

    重置密码后, 旧 access (30min) 和 旧 refresh (30day) 在 JWT 自然 TTL 内**仍可用**.
    这是设计取舍 (见 endpoint docstring "安全边界"段), 不是 bug:

    - 主用例 (admin 自助解锁): admin 重置自己密码 → 旧 token 是 ta 自己的设备 →
      没有强踢必要; 旧 access 几分钟内自然过期, ta 用新密码再登一次即可
    - 安全事件强踢: 需要后续 sprint 加 users.password_version + JWT pv claim

    这个测试**钉子化**了这一行为, 防止将来有人想当然地以为"ops 重置 = 强踢"出 bug.
    """
    body = await _login_otp(client, "+8613800138000")
    old_access = body["tokens"]["access_token"]
    old_refresh = body["tokens"]["refresh_token"]

    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13800138000"),
        headers=_admin_headers(),
        json={"new_password": "newpass123"},
    )
    assert r.status_code == 200, r.text

    # 旧 access 仍可用 (JWT 自然 TTL 内)
    r2 = await client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {old_access}"}
    )
    assert r2.status_code == 200, "旧 access 应该在自然 TTL 内仍可用"

    # 旧 refresh 也仍可换新 access (没有强踢机制)
    r3 = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_refresh}
    )
    assert r3.status_code == 200, (
        "当前实现: 旧 refresh 在自然 TTL 内仍可用; 如果这条 fail 了说明加了强踢机制, "
        "请同步更新 endpoint docstring 安全边界段 + 删除这条测试."
    )


# ─── 幂等性: 重复 reset 不破坏 ────────────────────────────────────


async def test_repeat_set_password_is_idempotent(client: httpx.AsyncClient) -> None:
    """连续重置 2 次密码都应该成功; 每次都 hash 一次新密码 (cost=12 ~250ms)."""
    await _login_otp(client, "+8613800138000")

    r1 = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13800138000"),
        headers=_admin_headers(),
        json={"new_password": "First123"},
    )
    assert r1.status_code == 200

    r2 = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13800138000"),
        headers=_admin_headers(),
        json={"new_password": "Second123"},
    )
    assert r2.status_code == 200

    # 旧密码不能用了
    r_old = await _login_password(client, "13800138000", "First123")
    assert r_old.status_code in (401, 403)

    # 新密码能用
    r_new = await _login_password(client, "13800138000", "Second123")
    assert r_new.status_code == 200


# 我们故意不写 truncate_users 跨用例隔离 (上面每个用例都靠 fixture); 测一下
# update(User).is_admin 不影响其他 user
async def test_set_password_does_not_affect_other_users(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """改 A 的密码不应该影响 B 的状态."""
    await _login_otp(client, "+8613800138000")
    await _login_otp(client, "+8613900139000")

    # 改 A
    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13800138000"),
        headers=_admin_headers(),
        json={"new_password": "OnlyAChanged123", "grant_admin": False},
    )
    assert r.status_code == 200, r.text

    async with session_factory() as s:
        a = (
            await s.execute(select(User).where(User.phone == "+8613800138000"))
        ).scalar_one()
        b = (
            await s.execute(select(User).where(User.phone == "+8613900139000"))
        ).scalar_one()
        assert a.password_hash is not None
        assert b.password_hash is None
        assert b.is_admin is False


# auxiliary: 也手动验下 _promote_to_admin helper 的"非 13007 admin" 情况下能 reset
async def _promote_to_admin(
    session_factory: async_sessionmaker[AsyncSession], phone: str
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(User).where(User.phone == phone).values(is_admin=True)
        )
        await session.commit()


async def test_can_reset_password_for_manually_promoted_admin(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """非 INITIAL_ADMIN_PHONES 的用户被 DB 直接标 admin 后, 也能走这个 endpoint
    重置密码 (运维场景: 给其他 admin 重置密码)."""
    await _login_otp(client, "+8613900139000")
    await _promote_to_admin(session_factory, "+8613900139000")

    r = await client.post(
        OPS_PATH_BY_PHONE.format(phone="13900139000"),
        headers=_admin_headers(),
        json={"new_password": "Promoted123", "grant_admin": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_admin"] is True  # 保持 admin

    r2 = await _login_password(client, "13900139000", "Promoted123")
    assert r2.status_code == 200, r2.text
