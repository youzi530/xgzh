"""Sprint 11 BE-S11-D04: /admin/knowledge/articles 端到端测试.

覆盖:
- RBAC: 401 / 403
- POST 新建: 字段校验 (slug 格式 / category 枚举 / level 范围)
- POST slug 重复 → 409
- GET 列表: filter (q / category / level / is_published) + 分页
- GET 详情: 含未发布草稿
- PATCH 部分更新: 单字段独立改 (title / is_published / content_md)
- PATCH slug 不可改 (即便传也忽略)
- DELETE: 硬删 + 404
- 用户公开 API: 只能看 is_published=true (不影响 admin 视图)
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


async def _admin_token(client: httpx.AsyncClient) -> str:
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


async def _regular_token(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker,
    phone: str,
) -> str:
    full_phone = phone if phone.startswith("+") else f"+86{phone}"
    await otp_service.store_otp(full_phone, "111111", ttl_seconds=300)
    r = await client.post(
        "/api/v1/auth/login/phone",
        json={"phone": full_phone, "code": "111111"},
    )
    assert r.status_code == 200
    user_id = uuid.UUID(r.json()["user"]["user_id"])
    async with session_factory() as s:
        await s.execute(
            update(User)
            .where(User.user_id == user_id)
            .values(created_at=datetime.now(UTC) - timedelta(days=60))
        )
        await s.commit()
    return r.json()["tokens"]["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _payload(slug: str, **overrides) -> dict:
    """生成合法 POST body. 让具体测试用例 override 字段."""
    base = {
        "slug": slug,
        "title": f"测试文章 {slug}",
        "category": "general",
        "tags": ["test"],
        "level": 1,
        "content_md": f"# {slug}\n\n这是测试内容.",
        "is_published": False,
        "source": "curated",
    }
    base.update(overrides)
    return base


# ─── 1. RBAC ────────────────────────────────────────────────


async def test_unauthenticated_returns_401(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/v1/admin/knowledge/articles")
    assert r.status_code == 401


async def test_non_admin_returns_403(
    client: httpx.AsyncClient, session_factory: async_sessionmaker
) -> None:
    t = await _regular_token(client, session_factory, "13000088001")
    r = await client.get("/api/v1/admin/knowledge/articles", headers=_h(t))
    assert r.status_code == 403


# ─── 2. POST create ─────────────────────────────────────────


async def test_create_article_ok(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    body = _payload("test-create-ok", title="新建 OK", category="hk", level=2)
    r = await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=body,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["slug"] == "test-create-ok"
    assert data["title"] == "新建 OK"
    assert data["category"] == "hk"
    assert data["level"] == 2
    assert data["is_published"] is False  # 默认草稿
    assert data["view_count"] == 0


async def test_create_duplicate_slug_returns_409(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    body = _payload("test-dup-slug")
    r1 = await client.post(
        "/api/v1/admin/knowledge/articles", headers=_h(admin), json=body
    )
    assert r1.status_code == 201
    r2 = await client.post(
        "/api/v1/admin/knowledge/articles", headers=_h(admin), json=body
    )
    assert r2.status_code == 409


async def test_create_invalid_slug_format(client: httpx.AsyncClient) -> None:
    """slug 必须是 ``[a-z0-9][a-z0-9-_]*``."""
    admin = await _admin_token(client)
    body = _payload("Test-Upper-Case")  # 大写违法
    r = await client.post(
        "/api/v1/admin/knowledge/articles", headers=_h(admin), json=body
    )
    assert r.status_code == 422


async def test_create_invalid_category(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    body = _payload("test-bad-cat", category="weird")
    r = await client.post(
        "/api/v1/admin/knowledge/articles", headers=_h(admin), json=body
    )
    assert r.status_code == 422


async def test_create_invalid_level(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    body = _payload("test-bad-level", level=5)
    r = await client.post(
        "/api/v1/admin/knowledge/articles", headers=_h(admin), json=body
    )
    assert r.status_code == 422


# ─── 3. GET list ────────────────────────────────────────────


async def test_list_includes_unpublished(client: httpx.AsyncClient) -> None:
    """admin 列表含 is_published=false."""
    admin = await _admin_token(client)
    # 创建一个草稿
    await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload("test-list-draft", is_published=False),
    )
    # 创建一个发布
    await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload("test-list-published", is_published=True),
    )

    r = await client.get(
        "/api/v1/admin/knowledge/articles?q=test-list", headers=_h(admin)
    )
    assert r.status_code == 200
    body = r.json()
    slugs = [item["slug"] for item in body["items"]]
    assert "test-list-draft" in slugs
    assert "test-list-published" in slugs


async def test_list_filter_is_published(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload("test-filter-draft-1", is_published=False),
    )
    await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload("test-filter-pub-1", is_published=True),
    )

    r1 = await client.get(
        "/api/v1/admin/knowledge/articles?q=test-filter&is_published=true",
        headers=_h(admin),
    )
    assert {item["slug"] for item in r1.json()["items"]} == {"test-filter-pub-1"}

    r2 = await client.get(
        "/api/v1/admin/knowledge/articles?q=test-filter&is_published=false",
        headers=_h(admin),
    )
    assert {item["slug"] for item in r2.json()["items"]} == {"test-filter-draft-1"}


async def test_list_filter_category(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload("test-cat-hk", category="hk"),
    )
    await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload("test-cat-cn", category="cn"),
    )
    r = await client.get(
        "/api/v1/admin/knowledge/articles?q=test-cat&category=hk",
        headers=_h(admin),
    )
    assert {item["slug"] for item in r.json()["items"]} == {"test-cat-hk"}


async def test_list_search_by_title(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload(
            "test-title-keyword",
            title="独有标题关键字 UniqueTitleKey42",
        ),
    )
    r = await client.get(
        "/api/v1/admin/knowledge/articles?q=UniqueTitleKey42",
        headers=_h(admin),
    )
    assert r.json()["total"] == 1


# ─── 4. GET detail ──────────────────────────────────────────


async def test_get_detail_includes_content(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    r_create = await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload(
            "test-detail",
            content_md="# 详情正文\n\n详细内容.",
        ),
    )
    aid = r_create.json()["id"]
    r = await client.get(
        f"/api/v1/admin/knowledge/articles/{aid}", headers=_h(admin)
    )
    assert r.status_code == 200
    body = r.json()
    assert "# 详情正文" in body["content_md"]
    assert body["is_published"] is False


async def test_get_detail_not_found(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    fake = "00000000-0000-0000-0000-000000000000"
    r = await client.get(
        f"/api/v1/admin/knowledge/articles/{fake}", headers=_h(admin)
    )
    assert r.status_code == 404


# ─── 5. PATCH update ────────────────────────────────────────


async def test_patch_title(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    r_create = await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload("test-patch-title", title="原始标题"),
    )
    aid = r_create.json()["id"]
    r = await client.patch(
        f"/api/v1/admin/knowledge/articles/{aid}",
        headers=_h(admin),
        json={"title": "新标题"},
    )
    assert r.status_code == 200
    assert r.json()["title"] == "新标题"
    # slug 没变
    assert r.json()["slug"] == "test-patch-title"


async def test_patch_publish_toggle(client: httpx.AsyncClient) -> None:
    """改 is_published=true 后用户公开 API 应能查到."""
    admin = await _admin_token(client)
    r_create = await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload("test-publish-toggle", is_published=False),
    )
    aid = r_create.json()["id"]
    r = await client.patch(
        f"/api/v1/admin/knowledge/articles/{aid}",
        headers=_h(admin),
        json={"is_published": True},
    )
    assert r.json()["is_published"] is True

    # 用户公开 API: 此时 slug 应能查到
    r_pub = await client.get("/api/v1/knowledge/test-publish-toggle")
    assert r_pub.status_code == 200


async def test_patch_slug_ignored(client: httpx.AsyncClient) -> None:
    """传 slug 字段应被无视 (schema 里没这个字段 → Pydantic ignore)."""
    admin = await _admin_token(client)
    r_create = await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload("test-slug-immutable"),
    )
    aid = r_create.json()["id"]
    r = await client.patch(
        f"/api/v1/admin/knowledge/articles/{aid}",
        headers=_h(admin),
        json={"slug": "hacked-slug", "title": "改个标题"},
    )
    assert r.status_code == 200
    assert r.json()["slug"] == "test-slug-immutable"  # 没改
    assert r.json()["title"] == "改个标题"


async def test_patch_not_found(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    fake = "00000000-0000-0000-0000-000000000000"
    r = await client.patch(
        f"/api/v1/admin/knowledge/articles/{fake}",
        headers=_h(admin),
        json={"title": "新标题"},
    )
    assert r.status_code == 404


async def test_patch_empty_body_returns_current(client: httpx.AsyncClient) -> None:
    """空 body PATCH 应直接返当前. 不报错."""
    admin = await _admin_token(client)
    r_create = await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload("test-patch-empty"),
    )
    aid = r_create.json()["id"]
    r = await client.patch(
        f"/api/v1/admin/knowledge/articles/{aid}",
        headers=_h(admin),
        json={},
    )
    assert r.status_code == 200
    assert r.json()["slug"] == "test-patch-empty"


# ─── 6. DELETE ──────────────────────────────────────────────


async def test_delete_article(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    r_create = await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload("test-delete-once"),
    )
    aid = r_create.json()["id"]
    r = await client.delete(
        f"/api/v1/admin/knowledge/articles/{aid}", headers=_h(admin)
    )
    assert r.status_code == 204

    r2 = await client.get(
        f"/api/v1/admin/knowledge/articles/{aid}", headers=_h(admin)
    )
    assert r2.status_code == 404


async def test_delete_not_found(client: httpx.AsyncClient) -> None:
    admin = await _admin_token(client)
    fake = "00000000-0000-0000-0000-000000000000"
    r = await client.delete(
        f"/api/v1/admin/knowledge/articles/{fake}", headers=_h(admin)
    )
    assert r.status_code == 404


# ─── 7. 用户公开 API 验证: 只能看 published ──────────────────


async def test_public_api_hides_drafts(client: httpx.AsyncClient) -> None:
    """admin 建草稿后, 用户公开 GET /knowledge/{slug} 应 404."""
    admin = await _admin_token(client)
    await client.post(
        "/api/v1/admin/knowledge/articles",
        headers=_h(admin),
        json=_payload("test-public-draft", is_published=False),
    )
    r = await client.get("/api/v1/knowledge/test-public-draft")
    assert r.status_code == 404
