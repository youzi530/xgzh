"""BE-S6-004 知识库端到端集成测.

覆盖 (spec/13 §AC):
- 列表 page + filter (category / level / tag)
- 详情按 slug 取
- 详情自动 view_count++ (异步, 但读 PG 能看到)
- categories 端点返回 hk/cn/general 三类 + 计数
- 未发布的文章不出现在任何读路径
- slug 不存在 → 404
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import KnowledgeArticle

pytestmark = [pytest.mark.asyncio, pytest.mark.db]


# ─── helpers ────────────────────────────────────────────────────────────


async def _seed_articles(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """植入 5 篇 fixture: hk×2 (published, 1 入门 + 1 进阶) + cn×1 + general×1 + draft×1."""
    async with session_factory() as session:
        articles = [
            KnowledgeArticle(
                slug="hk-key-dates",
                title="港股打新 5 个关键日期",
                category="hk",
                tags=["入门", "日期"],
                level=1,
                content_md="# 港股打新关键日期\n\n招股期 / 截止 / 公布 / 上市 / 暗盘.",
                view_count=0,
                is_published=True,
                source="curated",
            ),
            KnowledgeArticle(
                slug="hk-margin-strategy",
                title="港股孖展策略与杠杆控制",
                category="hk",
                tags=["进阶", "杠杆"],
                level=2,
                content_md="# 港股孖展\n\n杠杆 X 倍 = 利息 = 成本.",
                view_count=10,
                is_published=True,
                source="curated",
            ),
            KnowledgeArticle(
                slug="cn-rules",
                title="A 股打新基础规则",
                category="cn",
                tags=["入门", "规则"],
                level=1,
                content_md="# A 股打新\n\n市值 + T-2 + 摇号.",
                view_count=5,
                is_published=True,
                source="curated",
            ),
            KnowledgeArticle(
                slug="general-pe-pb",
                title="PE / PB 估值入门",
                category="general",
                tags=["入门", "估值"],
                level=1,
                content_md="# PE 与 PB\n\n通用估值常识.",
                view_count=20,
                is_published=True,
                source="curated",
            ),
            KnowledgeArticle(
                slug="hk-draft",
                title="(草稿) 未发布的港股文",
                category="hk",
                tags=["进阶"],
                level=2,
                content_md="# 草稿\n\n暂不发布.",
                view_count=0,
                is_published=False,
                source="curated",
            ),
        ]
        for a in articles:
            session.add(a)
        await session.commit()


# ─── 1. 列表 ────────────────────────────────────────────────────────────


async def test_list_returns_only_published(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_articles(session_factory)
    res = await client.get("/api/v1/knowledge")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 4  # 草稿不算
    slugs = {it["slug"] for it in body["items"]}
    assert "hk-draft" not in slugs


async def test_list_does_not_return_content_md(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """list 端点出于网络优化, 不回 content_md / toc_json."""
    await _seed_articles(session_factory)
    res = await client.get("/api/v1/knowledge")
    body = res.json()
    for item in body["items"]:
        assert "content_md" not in item
        assert "toc_json" not in item


async def test_list_filter_by_category(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_articles(session_factory)
    res = await client.get("/api/v1/knowledge?category=hk")
    body = res.json()
    assert body["total"] == 2  # 2 篇 hk published
    cats = {it["category"] for it in body["items"]}
    assert cats == {"hk"}


async def test_list_filter_by_level(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_articles(session_factory)
    res = await client.get("/api/v1/knowledge?level=1")
    body = res.json()
    assert body["total"] == 3  # 3 篇 level=1 (hk + cn + general)


async def test_list_filter_by_tag(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_articles(session_factory)
    res = await client.get("/api/v1/knowledge?tag=入门")
    body = res.json()
    assert body["total"] == 3  # 3 篇带"入门"tag


async def test_list_pagination(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_articles(session_factory)
    res = await client.get("/api/v1/knowledge?page=1&page_size=2")
    body = res.json()
    assert body["total"] == 4
    assert len(body["items"]) == 2
    assert body["page"] == 1
    assert body["page_size"] == 2


async def test_list_invalid_category_rejected(client: httpx.AsyncClient) -> None:
    res = await client.get("/api/v1/knowledge?category=us")
    assert res.status_code == 422


# ─── 2. 详情 ────────────────────────────────────────────────────────────


async def test_get_article_returns_full_fields(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_articles(session_factory)
    res = await client.get("/api/v1/knowledge/hk-key-dates")
    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "hk-key-dates"
    assert "招股期" in body["content_md"]
    assert "tags" in body and "入门" in body["tags"]


async def test_get_unknown_slug_returns_404(client: httpx.AsyncClient) -> None:
    res = await client.get("/api/v1/knowledge/does-not-exist")
    assert res.status_code == 404


async def test_get_unpublished_returns_404(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """未发布的文章 (is_published=FALSE) 详情端点也 404."""
    await _seed_articles(session_factory)
    res = await client.get("/api/v1/knowledge/hk-draft")
    assert res.status_code == 404


async def test_get_article_increments_view_count(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """异步 +1; FastAPI BackgroundTasks 在 response 后跑, 用 PG 直查验证."""
    await _seed_articles(session_factory)
    # 拿 baseline view_count
    async with session_factory() as session:
        before = (
            await session.execute(
                select(KnowledgeArticle.view_count).where(
                    KnowledgeArticle.slug == "hk-key-dates"
                )
            )
        ).scalar_one()

    # GET 触发 BackgroundTask
    res = await client.get("/api/v1/knowledge/hk-key-dates")
    assert res.status_code == 200

    # async client 走 ASGI Transport, BackgroundTasks 已在响应前 await; 直读应 +1
    async with session_factory() as session:
        after = (
            await session.execute(
                select(KnowledgeArticle.view_count).where(
                    KnowledgeArticle.slug == "hk-key-dates"
                )
            )
        ).scalar_one()
    assert after == before + 1


# ─── 3. 分类计数 ────────────────────────────────────────────────────────


async def test_categories_returns_three_classes(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_articles(session_factory)
    res = await client.get("/api/v1/knowledge/categories")
    assert res.status_code == 200
    body = res.json()
    by_cat = {it["category"]: it for it in body["items"]}
    assert set(by_cat) == {"hk", "cn", "general"}
    assert by_cat["hk"]["count"] == 2
    assert by_cat["cn"]["count"] == 1
    assert by_cat["general"]["count"] == 1
    assert by_cat["hk"]["label"] == "港股打新"
    # grand_total = 2+1+1 = 4 (草稿不算)
    assert body["total"] == 4


async def test_categories_works_when_empty(client: httpx.AsyncClient) -> None:
    """没任何文章时, 三个 category 仍返回 count=0."""
    res = await client.get("/api/v1/knowledge/categories")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 0
    assert {it["category"] for it in body["items"]} == {"hk", "cn", "general"}
    for it in body["items"]:
        assert it["count"] == 0


# ─── 4. 公开 (无登录) ────────────────────────────────────────────────────


async def test_anonymous_can_read(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """知识库公开内容, 不要求登录 — 不带 Authorization header 也能读."""
    await _seed_articles(session_factory)
    list_res = await client.get("/api/v1/knowledge")
    assert list_res.status_code == 200
    detail_res = await client.get("/api/v1/knowledge/hk-key-dates")
    assert detail_res.status_code == 200


# ─── 5. UUID 不应出现在 url 中 ────────────────────────────────────────────


async def test_get_by_uuid_returns_404_not_500(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """误用 UUID 当 slug 也走 404 (无 ID 查文章, slug 不存在)."""
    await _seed_articles(session_factory)
    fake_uuid = str(uuid.uuid4())
    res = await client.get(f"/api/v1/knowledge/{fake_uuid}")
    assert res.status_code == 404
