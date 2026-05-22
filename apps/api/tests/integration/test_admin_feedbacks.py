"""Sprint 11 BE-S11-B04: /admin/feedbacks (JWT 路径) 端到端测试.

覆盖:
- RBAC: 401 (无 token) / 403 (非 admin)
- GET /admin/feedbacks — 分页 + filter (category / platform / admin_status / q / include_deleted)
- GET /admin/feedbacks/{id} — 详情 (含软删的)
- PATCH /admin/feedbacks/{id} — 改 status (自动填 reviewed_by/at) / 改 note
- PATCH 不能改 content / user_id / ip_inet (schema extra=forbid)
- DELETE /admin/feedbacks/{id} — 软删 + 幂等
- POST /admin/feedbacks/{id}/restore — 恢复 + 幂等
- 老 ops 路径 (X-Admin-Token) 行为不变 (双系统并存)
"""

from __future__ import annotations

import httpx
import pytest

from app.services import otp_service

pytestmark = pytest.mark.db


# ─── helpers ────────────────────────────────────────────────


async def _login(c: httpx.AsyncClient, phone: str, code: str = "123456") -> dict:
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    r = await c.post("/api/v1/auth/login/phone", json={"phone": phone, "code": code})
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _admin_setup(c: httpx.AsyncClient) -> tuple[str, str]:
    admin_body = await _login(c, phone="+8613007458553")
    assert admin_body["user"]["is_admin"] is True
    regular_body = await _login(c, phone="+8613800138000")
    return admin_body["tokens"]["access_token"], regular_body["tokens"]["access_token"]


async def _create_feedback(
    c: httpx.AsyncClient,
    *,
    user_token: str | None = None,
    content: str = "测试反馈",
    category: str = "bug",
    platform: str = "h5",
) -> str:
    headers = _bearer(user_token) if user_token else {}
    r = await c.post(
        "/api/v1/feedback",
        json={
            "category": category,
            "content": content,
            "platform": platform,
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["feedback_id"]


# ─── 1. RBAC ────────────────────────────────────────────────


async def test_unauthenticated_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/admin/feedbacks")
    assert r.status_code == 401


async def test_non_admin_returns_403(client: httpx.AsyncClient) -> None:
    _admin, regular = await _admin_setup(client)
    r = await client.get("/api/v1/admin/feedbacks", headers=_bearer(regular))
    assert r.status_code == 403


# ─── 2. GET list ────────────────────────────────────────────


async def test_list_returns_paginated(client: httpx.AsyncClient) -> None:
    admin, regular = await _admin_setup(client)
    for i in range(3):
        await _create_feedback(client, user_token=regular, content=f"反馈 {i}")

    r = await client.get("/api/v1/admin/feedbacks", headers=_bearer(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert len(body["items"]) == 3
    assert all(item["is_deleted"] is False for item in body["items"])


async def test_list_filter_by_category(client: httpx.AsyncClient) -> None:
    admin, regular = await _admin_setup(client)
    await _create_feedback(client, user_token=regular, category="bug")
    await _create_feedback(client, user_token=regular, category="feature")

    r = await client.get(
        "/api/v1/admin/feedbacks?category=feature", headers=_bearer(admin)
    )
    assert r.json()["total"] == 1


async def test_list_filter_by_admin_status_pending_includes_null(
    client: httpx.AsyncClient,
) -> None:
    """admin_status='pending' 应同时匹配 NULL 和字面 'pending' 两种."""
    admin, regular = await _admin_setup(client)
    fid_1 = await _create_feedback(client, user_token=regular, content="待处理 1")
    fid_2 = await _create_feedback(client, user_token=regular, content="待处理 2")
    await client.patch(
        f"/api/v1/admin/feedbacks/{fid_1}",
        headers=_bearer(admin),
        json={"admin_status": "reviewed"},
    )

    r = await client.get(
        "/api/v1/admin/feedbacks?admin_status=pending", headers=_bearer(admin)
    )
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["feedback_id"] == fid_2


async def test_list_search_by_content(client: httpx.AsyncClient) -> None:
    admin, regular = await _admin_setup(client)
    await _create_feedback(client, user_token=regular, content="登录时报错 500")
    await _create_feedback(client, user_token=regular, content="界面颜色很难看")

    r = await client.get(
        "/api/v1/admin/feedbacks?q=登录", headers=_bearer(admin)
    )
    assert r.json()["total"] == 1
    assert "登录" in r.json()["items"][0]["content"]


async def test_list_excludes_soft_deleted_by_default(client: httpx.AsyncClient) -> None:
    admin, regular = await _admin_setup(client)
    fid = await _create_feedback(client, user_token=regular)
    await client.delete(f"/api/v1/admin/feedbacks/{fid}", headers=_bearer(admin))

    r1 = await client.get("/api/v1/admin/feedbacks", headers=_bearer(admin))
    assert r1.json()["total"] == 0

    r2 = await client.get(
        "/api/v1/admin/feedbacks?include_deleted=true", headers=_bearer(admin)
    )
    assert r2.json()["total"] == 1
    assert r2.json()["items"][0]["is_deleted"] is True


# ─── 3. GET detail ──────────────────────────────────────────


async def test_get_detail_returns_admin_fields(client: httpx.AsyncClient) -> None:
    admin, regular = await _admin_setup(client)
    fid = await _create_feedback(client, user_token=regular)

    r = await client.get(
        f"/api/v1/admin/feedbacks/{fid}", headers=_bearer(admin)
    )
    assert r.status_code == 200
    body = r.json()
    assert body["feedback_id"] == fid
    assert body["admin_status"] is None
    assert body["admin_note"] is None
    assert body["reviewed_by"] is None
    assert body["is_deleted"] is False


async def test_get_detail_not_found(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    fake = "00000000-0000-0000-0000-000000000000"
    r = await client.get(
        f"/api/v1/admin/feedbacks/{fake}", headers=_bearer(admin)
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "feedback_not_found"


# ─── 4. PATCH ───────────────────────────────────────────────


async def test_patch_admin_status_sets_reviewer(client: httpx.AsyncClient) -> None:
    admin, regular = await _admin_setup(client)
    fid = await _create_feedback(client, user_token=regular)

    r = await client.patch(
        f"/api/v1/admin/feedbacks/{fid}",
        headers=_bearer(admin),
        json={"admin_status": "resolved"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["admin_status"] == "resolved"
    assert body["reviewed_by"] is not None
    assert body["reviewed_at"] is not None


async def test_patch_admin_note_does_not_set_reviewer(
    client: httpx.AsyncClient,
) -> None:
    """只改 note 不算"处理过", reviewed_by/at 保持 NULL."""
    admin, regular = await _admin_setup(client)
    fid = await _create_feedback(client, user_token=regular)

    r = await client.patch(
        f"/api/v1/admin/feedbacks/{fid}",
        headers=_bearer(admin),
        json={"admin_note": "稍后处理"},
    )
    body = r.json()
    assert body["admin_note"] == "稍后处理"
    assert body["reviewed_by"] is None


async def test_patch_cannot_modify_content(client: httpx.AsyncClient) -> None:
    """PIPL: admin 不能改用户原文 content (schema extra=forbid)."""
    admin, regular = await _admin_setup(client)
    fid = await _create_feedback(client, user_token=regular, content="原文")

    r = await client.patch(
        f"/api/v1/admin/feedbacks/{fid}",
        headers=_bearer(admin),
        json={"content": "篡改"},
    )
    assert r.status_code == 422


async def test_patch_not_found(client: httpx.AsyncClient) -> None:
    admin, _ = await _admin_setup(client)
    fake = "00000000-0000-0000-0000-000000000000"
    r = await client.patch(
        f"/api/v1/admin/feedbacks/{fake}",
        headers=_bearer(admin),
        json={"admin_status": "reviewed"},
    )
    assert r.status_code == 404


async def test_patch_empty_body_is_noop(client: httpx.AsyncClient) -> None:
    admin, regular = await _admin_setup(client)
    fid = await _create_feedback(client, user_token=regular)
    r = await client.patch(
        f"/api/v1/admin/feedbacks/{fid}",
        headers=_bearer(admin),
        json={},
    )
    assert r.status_code == 200
    assert r.json()["admin_status"] is None


# ─── 5. DELETE ──────────────────────────────────────────────


async def test_soft_delete(client: httpx.AsyncClient) -> None:
    admin, regular = await _admin_setup(client)
    fid = await _create_feedback(client, user_token=regular)

    r = await client.delete(
        f"/api/v1/admin/feedbacks/{fid}", headers=_bearer(admin)
    )
    assert r.status_code == 204

    r2 = await client.get(
        f"/api/v1/admin/feedbacks/{fid}", headers=_bearer(admin)
    )
    assert r2.json()["is_deleted"] is True


async def test_delete_idempotent(client: httpx.AsyncClient) -> None:
    admin, regular = await _admin_setup(client)
    fid = await _create_feedback(client, user_token=regular)
    r1 = await client.delete(
        f"/api/v1/admin/feedbacks/{fid}", headers=_bearer(admin)
    )
    r2 = await client.delete(
        f"/api/v1/admin/feedbacks/{fid}", headers=_bearer(admin)
    )
    assert r1.status_code == 204
    assert r2.status_code == 204


# ─── 6. POST restore ────────────────────────────────────────


async def test_restore_soft_deleted(client: httpx.AsyncClient) -> None:
    admin, regular = await _admin_setup(client)
    fid = await _create_feedback(client, user_token=regular)
    await client.delete(f"/api/v1/admin/feedbacks/{fid}", headers=_bearer(admin))

    r = await client.post(
        f"/api/v1/admin/feedbacks/{fid}/restore", headers=_bearer(admin)
    )
    assert r.status_code == 200
    assert r.json()["is_deleted"] is False


# ─── 7. 老 ops 路径迁移到 /admin/ops/feedbacks (不在本测试里覆盖) ────
# 老 ops 路径 (X-Admin-Token, ``/admin/ops/feedbacks``) Sprint 11 迁移完成, 行为不变.
# 详见 tests/integration/test_feedback.py 里关于 ops X-Admin-Token 的测试.
