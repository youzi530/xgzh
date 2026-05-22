"""Sprint 11 BE-S11-C04: /admin/community/posts 端到端测试.

覆盖:
- RBAC: 401 / 403
- GET 列表: 分页 + filter (status / visibility / category / q / has_reports)
- GET 详情: 含 deleted 状态
- PATCH status: 强制改 status, 自动填 reviewed_by/at
- PATCH visibility: 软隐藏 (status 不变)
- DELETE: 软删 + 幂等
- 安全: 不能改 content / user_id 等审计字段

实现技巧 (参考 ``test_community_e2e.py``):
- 反 spam 限流 60s 1 帖/用户. 测试用每个 user 只发 1 帖.
- 7d 只读: 用 ``_register_old_user`` SQL 回调 ``created_at`` -60d.
- ``initial_admin_phone`` 设置: +8613007458553 登录后即是 admin.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import User
from app.services import otp_service

pytestmark = pytest.mark.db


# ─── helpers ────────────────────────────────────────────────


async def _register_old_user(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    *,
    phone: str,
    code: str = "111111",
) -> tuple[uuid.UUID, str]:
    """OTP 注册 + 回调 created_at -60d, 绕过 7d 只读保护."""
    full_phone = phone if phone.startswith("+") else f"+86{phone}"
    await otp_service.store_otp(full_phone, code, ttl_seconds=300)
    resp = await client.post(
        "/api/v1/auth/login/phone", json={"phone": full_phone, "code": code}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    user_id = uuid.UUID(body["user"]["user_id"])
    token = body["tokens"]["access_token"]

    async with session_factory() as s:
        await s.execute(
            update(User)
            .where(User.user_id == user_id)
            .values(created_at=datetime.now(UTC) - timedelta(days=60))
        )
        await s.commit()
    return user_id, token


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_post(
    client: httpx.AsyncClient, token: str, content: str = "测试帖"
) -> str:
    r = await client.post(
        "/api/v1/community/posts",
        headers=_h(token),
        json={"content": content},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _admin_token(client: httpx.AsyncClient) -> str:
    """登录 ``initial_admin_phone`` 拿 admin token."""
    full_phone = "+8613007458553"
    await otp_service.store_otp(full_phone, "111111", ttl_seconds=300)
    r = await client.post(
        "/api/v1/auth/login/phone",
        json={"phone": full_phone, "code": "111111"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["is_admin"] is True
    return body["tokens"]["access_token"]


# ─── 1. RBAC ────────────────────────────────────────────────


async def test_unauthenticated_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/admin/community/posts")
    assert r.status_code == 401


async def test_non_admin_returns_403(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    _, token = await _register_old_user(
        client, session_factory, phone="13000099001"
    )
    r = await client.get("/api/v1/admin/community/posts", headers=_h(token))
    assert r.status_code == 403


# ─── 2. GET list ────────────────────────────────────────────


async def test_list_returns_all_status_including_deleted(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    admin = await _admin_token(client)
    _, u = await _register_old_user(client, session_factory, phone="13000099002")
    pid = await _create_post(client, u, content="content-list-deleted")
    # admin 把它改 deleted
    r = await client.delete(
        f"/api/v1/admin/community/posts/{pid}", headers=_h(admin)
    )
    assert r.status_code == 204

    r = await client.get(
        "/api/v1/admin/community/posts?q=content-list-deleted",
        headers=_h(admin),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "deleted"


async def test_list_filter_by_status(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    admin = await _admin_token(client)
    _, u1 = await _register_old_user(client, session_factory, phone="13000099003")
    pid_a = await _create_post(client, u1, content="content-A-status-filter")
    _, u2 = await _register_old_user(client, session_factory, phone="13000099004")
    await _create_post(client, u2, content="content-B-status-filter")

    await client.delete(
        f"/api/v1/admin/community/posts/{pid_a}", headers=_h(admin)
    )

    r1 = await client.get(
        "/api/v1/admin/community/posts?status=deleted&q=status-filter",
        headers=_h(admin),
    )
    assert r1.json()["total"] == 1

    r2 = await client.get(
        "/api/v1/admin/community/posts?status=published&q=status-filter",
        headers=_h(admin),
    )
    assert r2.json()["total"] == 1


async def test_list_search_by_content(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    admin = await _admin_token(client)
    _, u1 = await _register_old_user(client, session_factory, phone="13000099005")
    await _create_post(client, u1, content="content-keyword-tencent-financial")
    _, u2 = await _register_old_user(client, session_factory, phone="13000099006")
    await _create_post(client, u2, content="content-keyword-alibaba-shopping")

    r = await client.get(
        "/api/v1/admin/community/posts?q=tencent-financial",
        headers=_h(admin),
    )
    assert r.json()["total"] == 1


async def test_list_pagination(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    """3 个用户 3 帖, page_size=2 → page 1 返 2, page 2 返 1."""
    admin = await _admin_token(client)
    for i in range(3):
        _, u = await _register_old_user(
            client, session_factory, phone=f"1300009990{i + 7}"
        )
        await _create_post(client, u, content=f"content-pagination-{i}")

    r1 = await client.get(
        "/api/v1/admin/community/posts?page_size=2&page=1&q=content-pagination",
        headers=_h(admin),
    )
    body = r1.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2

    r2 = await client.get(
        "/api/v1/admin/community/posts?page_size=2&page=2&q=content-pagination",
        headers=_h(admin),
    )
    assert len(r2.json()["items"]) == 1


# ─── 3. GET detail ──────────────────────────────────────────


async def test_get_detail_returns_admin_fields(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    admin = await _admin_token(client)
    _, u = await _register_old_user(client, session_factory, phone="13000099020")
    pid = await _create_post(client, u, content="content-detail-fields")

    r = await client.get(
        f"/api/v1/admin/community/posts/{pid}", headers=_h(admin)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == pid
    assert body["status"] == "published"
    assert body["visibility"] == "public"
    assert body["reviewed_by"] is None
    assert body["reviewed_at"] is None
    assert body["content"] == "content-detail-fields"


async def test_get_detail_not_found(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    fake = "00000000-0000-0000-0000-000000000000"
    r = await client.get(
        f"/api/v1/admin/community/posts/{fake}", headers=_h(admin)
    )
    assert r.status_code == 404


async def test_get_detail_includes_deleted(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    """admin 详情应能看 deleted 帖 (用户 API 返 404)."""
    admin = await _admin_token(client)
    _, u = await _register_old_user(client, session_factory, phone="13000099021")
    pid = await _create_post(client, u, content="content-detail-deleted")
    await client.delete(
        f"/api/v1/admin/community/posts/{pid}", headers=_h(admin)
    )

    # admin 还看得到
    r = await client.get(
        f"/api/v1/admin/community/posts/{pid}", headers=_h(admin)
    )
    assert r.status_code == 200
    assert r.json()["status"] == "deleted"
    # 用户视角 404
    r2 = await client.get(f"/api/v1/community/posts/{pid}")
    assert r2.status_code == 404


# ─── 4. PATCH status ────────────────────────────────────────


async def test_patch_status_sets_reviewer(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    admin = await _admin_token(client)
    _, u = await _register_old_user(client, session_factory, phone="13000099030")
    pid = await _create_post(client, u, content="content-patch-status")

    r = await client.patch(
        f"/api/v1/admin/community/posts/{pid}/status",
        headers=_h(admin),
        json={"status": "pending", "reason": "需要复审"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["reviewed_by"] is not None
    assert body["reviewed_at"] is not None
    assert body["rejection_reason"] == "需要复审"


async def test_patch_status_invalid_value(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    admin = await _admin_token(client)
    _, u = await _register_old_user(client, session_factory, phone="13000099031")
    pid = await _create_post(client, u, content="content-patch-invalid")
    r = await client.patch(
        f"/api/v1/admin/community/posts/{pid}/status",
        headers=_h(admin),
        json={"status": "weird_status"},
    )
    assert r.status_code == 422


async def test_patch_status_not_found(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    fake = "00000000-0000-0000-0000-000000000000"
    r = await client.patch(
        f"/api/v1/admin/community/posts/{fake}/status",
        headers=_h(admin),
        json={"status": "pending"},
    )
    assert r.status_code == 404


# ─── 5. PATCH visibility ────────────────────────────────────


async def test_patch_visibility_soft_hide(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    """改 visibility=self_only → status 仍 published."""
    admin = await _admin_token(client)
    _, u = await _register_old_user(client, session_factory, phone="13000099040")
    pid = await _create_post(client, u, content="content-patch-visibility")

    r = await client.patch(
        f"/api/v1/admin/community/posts/{pid}/visibility",
        headers=_h(admin),
        json={"visibility": "self_only"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["visibility"] == "self_only"
    assert body["status"] == "published"  # 关键: status 不变
    assert body["reviewed_by"] is not None


async def test_patch_visibility_invalid(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    admin = await _admin_token(client)
    _, u = await _register_old_user(client, session_factory, phone="13000099041")
    pid = await _create_post(client, u, content="content-patch-visibility-bad")
    r = await client.patch(
        f"/api/v1/admin/community/posts/{pid}/visibility",
        headers=_h(admin),
        json={"visibility": "weird"},
    )
    assert r.status_code == 422


# ─── 6. DELETE ──────────────────────────────────────────────


async def test_delete_post(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    admin = await _admin_token(client)
    _, u = await _register_old_user(client, session_factory, phone="13000099050")
    pid = await _create_post(client, u, content="content-delete-once")
    r = await client.delete(
        f"/api/v1/admin/community/posts/{pid}", headers=_h(admin)
    )
    assert r.status_code == 204

    r2 = await client.get(
        f"/api/v1/admin/community/posts/{pid}", headers=_h(admin)
    )
    assert r2.json()["status"] == "deleted"


async def test_delete_idempotent(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    admin = await _admin_token(client)
    _, u = await _register_old_user(client, session_factory, phone="13000099051")
    pid = await _create_post(client, u, content="content-delete-idempotent")
    r1 = await client.delete(
        f"/api/v1/admin/community/posts/{pid}", headers=_h(admin)
    )
    r2 = await client.delete(
        f"/api/v1/admin/community/posts/{pid}", headers=_h(admin)
    )
    assert r1.status_code == 204
    assert r2.status_code == 204


async def test_delete_not_found(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    fake = "00000000-0000-0000-0000-000000000000"
    r = await client.delete(
        f"/api/v1/admin/community/posts/{fake}", headers=_h(admin)
    )
    assert r.status_code == 404


# ─── 7. 不能改 content ───────────────────────────────────────


async def test_status_update_does_not_modify_content(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    """PATCH status 不存在改 content 的字段, 验证原文不变."""
    admin = await _admin_token(client)
    _, u = await _register_old_user(client, session_factory, phone="13000099060")
    pid = await _create_post(client, u, content="原文-content-immutable")

    await client.patch(
        f"/api/v1/admin/community/posts/{pid}/status",
        headers=_h(admin),
        json={"status": "rejected", "reason": "违规"},
    )

    r = await client.get(
        f"/api/v1/admin/community/posts/{pid}", headers=_h(admin)
    )
    assert r.json()["content"] == "原文-content-immutable"
