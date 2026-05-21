"""Sprint 10 BE-S10-006: /admin/users 5 endpoint 端到端测试.

覆盖:
- GET /admin/users — 列表 + 搜索 + 分页 + filter
- GET /admin/users/{id} — 单用户详情 + 软删用户能看
- PATCH /admin/users/{id} — nickname / region / status; 不能改 phone/email/is_admin
- DELETE /admin/users/{id} — 软删, 幂等, 不能自删
- POST /admin/users/{id}/grant-vip — 加 VIP 时长, days 上限 365, reason 必填

依赖真 PG (XGZH_TEST_DATABASE_URL), 标 ``@pytest.mark.db``.
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
from app.adapters.sms import MockSMSAdapter, reset_sms_adapter, set_sms_adapter
from app.cache import InMemoryRedisClient, reset_redis_client, set_redis_client
from app.db.base import get_session
from app.db.models import User
from app.main import create_app
from app.services import otp_service

pytestmark = pytest.mark.db


# ─── fixtures (与 test_admin_rbac.py 同源) ───────────────────────


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
                "TRUNCATE users, auth_sessions, vip_memberships, invite_codes, "
                "vip_orders RESTART IDENTITY CASCADE"
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
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    r = await client.post(
        "/api/v1/auth/login/phone", json={"phone": phone, "code": code}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register_users(
    client: httpx.AsyncClient,
    phones: list[str],
) -> list[dict]:
    """批量走登录路径注册一批 users; 返回每个用户的 LoginResponse."""
    bodies = []
    for phone in phones:
        body = await _login(client, phone=phone)
        bodies.append(body)
    return bodies


async def _admin_setup(
    client: httpx.AsyncClient,
) -> tuple[str, str]:
    """注册初始 admin (13007458553) 与一个普通用户; 返 (admin_access, regular_access)."""
    admin_body = await _login(client, phone="+8613007458553")
    assert admin_body["user"]["is_admin"] is True
    regular_body = await _login(client, phone="+8613800138000")
    assert regular_body["user"]["is_admin"] is False
    return admin_body["tokens"]["access_token"], regular_body["tokens"]["access_token"]


# ─── 1. GET /admin/users — 列表 + 搜索 ────────────────────────────


async def test_list_users_returns_paginated_total(
    client: httpx.AsyncClient,
) -> None:
    admin_access, _ = await _admin_setup(client)
    await _register_users(
        client, ["+8613911111111", "+8613922222222", "+8613933333333"]
    )

    r = await client.get(
        "/api/v1/admin/users?page=1&page_size=20",
        headers=_bearer(admin_access),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 5  # admin + regular + 3 new
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert len(data["items"]) == 5


async def test_list_users_search_by_phone_partial(
    client: httpx.AsyncClient,
) -> None:
    admin_access, _ = await _admin_setup(client)
    await _register_users(client, ["+8613911111111"])

    r = await client.get(
        "/api/v1/admin/users?q=139111",
        headers=_bearer(admin_access),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    item = data["items"][0]
    # 脱敏后输出 — 不应有完整原 phone, 但应包含可识别片段
    assert item["phone_masked"] is not None
    assert "****" in item["phone_masked"]


async def test_list_users_search_by_nickname_partial(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_access, _ = await _admin_setup(client)
    body = await _login(client, phone="+8613944444444")

    # 改昵称 (走 admin patch — 顺手验证 admin 改昵称 works)
    target_id = body["user"]["user_id"]
    r = await client.patch(
        f"/api/v1/admin/users/{target_id}",
        json={"nickname": "张三老板"},
        headers=_bearer(admin_access),
    )
    assert r.status_code == 200, r.text
    assert r.json()["nickname"] == "张三老板"

    r = await client.get(
        "/api/v1/admin/users?q=张三",
        headers=_bearer(admin_access),
    )
    assert r.status_code == 200
    assert r.json()["total"] == 1


async def test_list_users_filter_admin_only(
    client: httpx.AsyncClient,
) -> None:
    admin_access, _ = await _admin_setup(client)
    await _register_users(client, ["+8613911111111"])

    r = await client.get(
        "/api/v1/admin/users?is_admin=true",
        headers=_bearer(admin_access),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1  # 只剩初始 admin
    assert data["items"][0]["is_admin"] is True


async def test_list_users_excludes_deleted_by_default(
    client: httpx.AsyncClient,
) -> None:
    admin_access, _ = await _admin_setup(client)
    body = await _login(client, phone="+8613911111111")
    target_id = body["user"]["user_id"]

    # 软删该用户
    r = await client.delete(
        f"/api/v1/admin/users/{target_id}",
        headers=_bearer(admin_access),
    )
    assert r.status_code == 204

    # 默认不返软删
    r = await client.get(
        "/api/v1/admin/users",
        headers=_bearer(admin_access),
    )
    assert r.status_code == 200
    assert r.json()["total"] == 2  # admin + regular (无 deleted)

    # 显式带 include_deleted 才能看到
    r = await client.get(
        "/api/v1/admin/users?include_deleted=true",
        headers=_bearer(admin_access),
    )
    assert r.status_code == 200
    assert r.json()["total"] == 3


# ─── 2. GET /admin/users/{id} — 单用户详情 ───────────────────────


async def test_get_user_detail_returns_aggregate(
    client: httpx.AsyncClient,
) -> None:
    admin_access, _ = await _admin_setup(client)
    body = await _login(client, phone="+8613922222222")
    target_id = body["user"]["user_id"]

    r = await client.get(
        f"/api/v1/admin/users/{target_id}",
        headers=_bearer(admin_access),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["user_id"] == target_id
    assert data["is_admin"] is False
    assert data["is_deleted"] is False
    assert data["invite_count"] == 0  # 该用户没邀请过别人
    # VIP trial 在注册时已发, 应该有 membership
    assert data["vip_status"] in ("trialing", "active")
    assert data["vip_end_at"] is not None


async def test_get_user_detail_not_found_returns_404(
    client: httpx.AsyncClient,
) -> None:
    admin_access, _ = await _admin_setup(client)
    fake_id = "00000000-0000-0000-0000-000000000000"
    r = await client.get(
        f"/api/v1/admin/users/{fake_id}",
        headers=_bearer(admin_access),
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "user_not_found"


async def test_get_user_detail_rejects_non_admin(
    client: httpx.AsyncClient,
) -> None:
    _, regular_access = await _admin_setup(client)
    body = await _login(client, phone="+8613922222222")
    r = await client.get(
        f"/api/v1/admin/users/{body['user']['user_id']}",
        headers=_bearer(regular_access),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "admin_required"


# ─── 3. PATCH /admin/users/{id} — 编辑 ──────────────────────────


async def test_patch_user_updates_nickname_and_status(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_access, _ = await _admin_setup(client)
    body = await _login(client, phone="+8613911111111")
    target_id = body["user"]["user_id"]

    r = await client.patch(
        f"/api/v1/admin/users/{target_id}",
        json={"nickname": "新昵称", "status": 0},
        headers=_bearer(admin_access),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["nickname"] == "新昵称"
    assert data["status"] == 0

    # DB 落库验证
    async with session_factory() as session:
        u = (
            await session.execute(
                select(User).where(User.phone == "+8613911111111")
            )
        ).scalar_one()
        assert u.nickname == "新昵称"
        assert u.status == 0


async def test_patch_user_rejects_phone_or_is_admin(
    client: httpx.AsyncClient,
) -> None:
    """schema 只接 nickname/region/status; 其它字段被 Pydantic 忽略 (不会落库)."""
    admin_access, _ = await _admin_setup(client)
    body = await _login(client, phone="+8613922222222")
    target_id = body["user"]["user_id"]

    # 试图 patch is_admin / phone — 后端 schema 不接, Pydantic 忽略 extra 默认行为
    r = await client.patch(
        f"/api/v1/admin/users/{target_id}",
        json={
            "is_admin": True,
            "phone": "+8613199999999",
            "nickname": "改昵称",  # 这个会生效
        },
        headers=_bearer(admin_access),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["nickname"] == "改昵称"
    assert data["is_admin"] is False  # 没改


async def test_patch_user_self_status_change_forbidden(
    client: httpx.AsyncClient,
) -> None:
    """admin 改自己 status → 403 防自锁."""
    admin_access, _ = await _admin_setup(client)
    # 先拿 admin 自己的 user_id
    r = await client.get("/api/v1/me", headers=_bearer(admin_access))
    admin_id = r.json()["user_id"]

    r = await client.patch(
        f"/api/v1/admin/users/{admin_id}",
        json={"status": 0},
        headers=_bearer(admin_access),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "cannot_demote_self"


async def test_patch_user_empty_body_returns_400(
    client: httpx.AsyncClient,
) -> None:
    admin_access, _ = await _admin_setup(client)
    body = await _login(client, phone="+8613922222222")

    r = await client.patch(
        f"/api/v1/admin/users/{body['user']['user_id']}",
        json={},
        headers=_bearer(admin_access),
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "no_change"


# ─── 4. DELETE /admin/users/{id} — 软删 ─────────────────────────


async def test_delete_user_soft_only_sets_deleted_at(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_access, _ = await _admin_setup(client)
    body = await _login(client, phone="+8613922222222")
    target_id = body["user"]["user_id"]

    r = await client.delete(
        f"/api/v1/admin/users/{target_id}",
        headers=_bearer(admin_access),
    )
    assert r.status_code == 204

    # 行还在 (软删), deleted_at NOT NULL, status=0
    async with session_factory() as session:
        u = (
            await session.execute(
                select(User).where(User.phone == "+8613922222222")
            )
        ).scalar_one()
        assert u.deleted_at is not None
        assert u.status == 0


async def test_delete_user_idempotent(
    client: httpx.AsyncClient,
) -> None:
    """重复软删 — 第二次仍 204 (幂等)."""
    admin_access, _ = await _admin_setup(client)
    body = await _login(client, phone="+8613922222222")
    target_id = body["user"]["user_id"]

    r1 = await client.delete(
        f"/api/v1/admin/users/{target_id}",
        headers=_bearer(admin_access),
    )
    assert r1.status_code == 204
    r2 = await client.delete(
        f"/api/v1/admin/users/{target_id}",
        headers=_bearer(admin_access),
    )
    assert r2.status_code == 204


async def test_delete_user_self_forbidden(
    client: httpx.AsyncClient,
) -> None:
    """admin 删自己 → 403 cannot_delete_self."""
    admin_access, _ = await _admin_setup(client)
    r = await client.get("/api/v1/me", headers=_bearer(admin_access))
    admin_id = r.json()["user_id"]

    r = await client.delete(
        f"/api/v1/admin/users/{admin_id}",
        headers=_bearer(admin_access),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "cannot_delete_self"


async def test_delete_user_not_found_returns_404(
    client: httpx.AsyncClient,
) -> None:
    admin_access, _ = await _admin_setup(client)
    fake_id = "00000000-0000-0000-0000-000000000000"
    r = await client.delete(
        f"/api/v1/admin/users/{fake_id}",
        headers=_bearer(admin_access),
    )
    assert r.status_code == 404


# ─── 5. POST /admin/users/{id}/grant-vip — 加 VIP 时长 ──────────


async def test_grant_vip_extends_membership(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin_access, _ = await _admin_setup(client)
    body = await _login(client, phone="+8613922222222")
    target_id = body["user"]["user_id"]

    # 先看现在 vip end_at
    r = await client.get(
        f"/api/v1/admin/users/{target_id}",
        headers=_bearer(admin_access),
    )
    initial_end = r.json()["vip_end_at"]
    assert initial_end is not None

    # 加 30 天
    r = await client.post(
        f"/api/v1/admin/users/{target_id}/grant-vip",
        json={"days": 30, "reason": "测试加VIP"},
        headers=_bearer(admin_access),
    )
    assert r.status_code == 200, r.text
    new_end = r.json()["vip_end_at"]
    assert new_end > initial_end  # 字符串比较 ISO 8601 时序稳定


async def test_grant_vip_non_idempotent(
    client: httpx.AsyncClient,
) -> None:
    """连续 2 次加 N 天 = 2N 天 (拍板: 非幂等)."""
    admin_access, _ = await _admin_setup(client)
    body = await _login(client, phone="+8613922222222")
    target_id = body["user"]["user_id"]

    r = await client.get(
        f"/api/v1/admin/users/{target_id}",
        headers=_bearer(admin_access),
    )
    initial_end = r.json()["vip_end_at"]

    for _ in range(2):
        r = await client.post(
            f"/api/v1/admin/users/{target_id}/grant-vip",
            json={"days": 10, "reason": "测试加VIP"},
            headers=_bearer(admin_access),
        )
        assert r.status_code == 200, r.text

    r = await client.get(
        f"/api/v1/admin/users/{target_id}",
        headers=_bearer(admin_access),
    )
    final_end = r.json()["vip_end_at"]
    # final_end 比 initial_end 多约 20 天 — 两次堆叠
    assert final_end > initial_end
    # 简单时间差校验: ISO 字符串比较够, 不精确算秒数


async def test_grant_vip_rejects_days_over_365(
    client: httpx.AsyncClient,
) -> None:
    admin_access, _ = await _admin_setup(client)
    body = await _login(client, phone="+8613922222222")
    target_id = body["user"]["user_id"]

    r = await client.post(
        f"/api/v1/admin/users/{target_id}/grant-vip",
        json={"days": 9999, "reason": "测试加VIP"},
        headers=_bearer(admin_access),
    )
    assert r.status_code == 422


async def test_grant_vip_requires_reason(
    client: httpx.AsyncClient,
) -> None:
    admin_access, _ = await _admin_setup(client)
    body = await _login(client, phone="+8613922222222")
    target_id = body["user"]["user_id"]

    # 短 reason 被 min_length=2 挡
    r = await client.post(
        f"/api/v1/admin/users/{target_id}/grant-vip",
        json={"days": 30, "reason": "a"},
        headers=_bearer(admin_access),
    )
    assert r.status_code == 422

    # 缺 reason
    r = await client.post(
        f"/api/v1/admin/users/{target_id}/grant-vip",
        json={"days": 30},
        headers=_bearer(admin_access),
    )
    assert r.status_code == 422


async def test_grant_vip_rejects_deleted_user(
    client: httpx.AsyncClient,
) -> None:
    """软删用户禁加 VIP — 404 (admin 应先恢复或换人)."""
    admin_access, _ = await _admin_setup(client)
    body = await _login(client, phone="+8613922222222")
    target_id = body["user"]["user_id"]

    # 软删
    r = await client.delete(
        f"/api/v1/admin/users/{target_id}",
        headers=_bearer(admin_access),
    )
    assert r.status_code == 204

    # 尝试给软删用户加 VIP
    r = await client.post(
        f"/api/v1/admin/users/{target_id}/grant-vip",
        json={"days": 30, "reason": "测试加VIP"},
        headers=_bearer(admin_access),
    )
    assert r.status_code == 404


async def test_grant_vip_rejects_non_admin(
    client: httpx.AsyncClient,
) -> None:
    _, regular_access = await _admin_setup(client)
    body = await _login(client, phone="+8613922222222")
    target_id = body["user"]["user_id"]

    r = await client.post(
        f"/api/v1/admin/users/{target_id}/grant-vip",
        json={"days": 30, "reason": "测试加VIP"},
        headers=_bearer(regular_access),
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "admin_required"
