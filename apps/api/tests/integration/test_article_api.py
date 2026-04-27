"""BE-S3-006 文章列表 / 详情 / 全文搜索 端到端集成测.

≥ 12 条覆盖 (spec/10 §AC):

列表 API ``GET /articles``:
1.  default — 默认 ``market=all sentiment=all`` 列出全部 (排除 child)
2.  market 筛选 — HK / A 隔离
3.  sentiment 筛选 — bullish / neutral / bearish
4.  source 筛选 — 雪球 / 智通财经
5.  ipo_code 筛选 — JSONB @> 走 GIN 索引
6.  sort_by=hot_score — 排序切换
7.  分页 — page=2 size=2 拿到第 3, 4 篇
8.  topic 折叠 — child 不出现在列表

详情 API ``GET /articles/{article_id}``:
9.  parent → 详情 + related_articles 拿 child
10. child → 自动重定向到 parent
11. 不存在 ID → 404
12. 非法 UUID 字符串 → 404 (而非 500)

搜索 API ``GET /search/articles``:
13. 中文 query — "招股" 命中 "招股说明书"
14. 中英文混合 query — "subscription"
15. 空 query → 400/422 (路由层校验)
16. q 全停用词 → 200 + items=[]

缓存:
17. 缓存 TTL 命中 — list 二次调用 mock _list_articles_db 不被打
18. invalidate_namespace 清缓存 — 写入新文章后再调列表回源

设计目的:
- 6, 8, 10 是 BE-S3-003 + BE-S3-006 联动的关键验证点 (折叠 + 反查)
- 12 是 BE-S2-002 防御性编程经验 (UUID 失败必须 404 不能 500)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache import invalidate_namespace
from app.db.models import Article, ArticleTopic
from app.services import article_service

pytestmark = pytest.mark.db


# ─── helpers ───────────────────────────────────────────────────────────────


async def _insert_article(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    title: str,
    summary: str = "",
    market: str = "HK",
    sentiment: str | None = "bullish",
    source_name: str = "雪球",
    related_ipos: list[dict[str, str]] | None = None,
    hot_score: float = 100.0,
    published_offset_minutes: int = 0,
    keywords: list[str] | None = None,
) -> uuid.UUID:
    async with session_factory() as s:
        a = Article(
            title=title,
            summary=summary,
            source_name=source_name,
            source_credibility=2,
            original_url=f"https://x.com/p/{uuid.uuid4()}",
            market=market,
            related_ipos=related_ipos or [],
            sentiment=sentiment,
            sentiment_score=Decimal("0.6") if sentiment else None,
            keywords=keywords or [],
            hot_score=Decimal(str(hot_score)),
            is_full_text_available=True,
            published_at=datetime.now(UTC) - timedelta(minutes=published_offset_minutes),
        )
        s.add(a)
        await s.commit()
        await s.refresh(a)
        return a.article_id


async def _link_child(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    parent_id: uuid.UUID,
    child_id: uuid.UUID,
) -> None:
    async with session_factory() as s:
        s.add(
            ArticleTopic(
                parent_article_id=parent_id,
                child_article_id=child_id,
                simhash_distance=2,
            )
        )
        await s.commit()


# ─── 1. 列表: default 全列 ─────────────────────────────────────────────────


async def test_list_articles_default_returns_all(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    for i in range(5):
        await _insert_article(
            session_factory,
            title=f"标题 {i}",
            summary=f"摘要 {i}",
            published_offset_minutes=i,
        )

    r = await client.get("/api/v1/articles")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 5
    assert len(data["items"]) == 5
    assert data["page"] == 1
    assert data["size"] == 20
    # 默认按 published_at DESC
    assert data["items"][0]["title"] == "标题 0"


# ─── 2. 列表: market 筛选 ─────────────────────────────────────────────────


async def test_list_articles_filter_by_market(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    for i in range(3):
        await _insert_article(
            session_factory, title=f"HK {i}", market="HK"
        )
    for i in range(2):
        await _insert_article(
            session_factory, title=f"A {i}", market="A"
        )

    r = await client.get("/api/v1/articles", params={"market": "HK"})
    assert r.json()["total"] == 3
    r = await client.get("/api/v1/articles", params={"market": "A"})
    assert r.json()["total"] == 2
    r = await client.get("/api/v1/articles", params={"market": "all"})
    assert r.json()["total"] == 5


# ─── 3. 列表: sentiment 筛选 ──────────────────────────────────────────────


async def test_list_articles_filter_by_sentiment(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _insert_article(session_factory, title="多", sentiment="bullish")
    await _insert_article(session_factory, title="空", sentiment="bearish")
    await _insert_article(session_factory, title="中", sentiment="neutral")
    await _insert_article(session_factory, title="未打标", sentiment=None)

    r = await client.get("/api/v1/articles", params={"sentiment": "bullish"})
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["title"] == "多"
    r = await client.get("/api/v1/articles", params={"sentiment": "bearish"})
    assert r.json()["total"] == 1
    r = await client.get("/api/v1/articles", params={"sentiment": "all"})
    # all 含未打标 (sentiment=NULL) 共 4 条
    assert r.json()["total"] == 4


# ─── 4. 列表: source 筛选 ─────────────────────────────────────────────────


async def test_list_articles_filter_by_source(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _insert_article(session_factory, title="A1", source_name="雪球")
    await _insert_article(session_factory, title="A2", source_name="雪球")
    await _insert_article(session_factory, title="B1", source_name="智通财经")

    r = await client.get("/api/v1/articles", params={"source": "雪球"})
    assert r.json()["total"] == 2
    r = await client.get("/api/v1/articles", params={"source": "智通财经"})
    assert r.json()["total"] == 1


# ─── 5. 列表: ipo_code JSONB GIN ──────────────────────────────────────────


async def test_list_articles_filter_by_ipo_code(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    related_a = [{"code": "00700.HK", "market": "HK", "name": "腾讯"}]
    related_b = [{"code": "00388.HK", "market": "HK", "name": "港交所"}]
    await _insert_article(session_factory, title="腾讯 1", related_ipos=related_a)
    await _insert_article(session_factory, title="腾讯 2", related_ipos=related_a)
    await _insert_article(session_factory, title="港交所", related_ipos=related_b)
    await _insert_article(session_factory, title="无关")

    r = await client.get("/api/v1/articles", params={"ipo_code": "00700.HK"})
    assert r.json()["total"] == 2
    r = await client.get("/api/v1/articles", params={"ipo_code": "00388.HK"})
    assert r.json()["total"] == 1


# ─── 6. 列表: sort_by=hot_score ───────────────────────────────────────────


async def test_list_articles_sort_by_hot_score(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _insert_article(session_factory, title="低", hot_score=10.0)
    await _insert_article(session_factory, title="中", hot_score=50.0)
    await _insert_article(session_factory, title="高", hot_score=100.0)

    r = await client.get("/api/v1/articles", params={"sort_by": "hot_score"})
    titles = [it["title"] for it in r.json()["items"]]
    assert titles == ["高", "中", "低"]


# ─── 7. 列表: 分页 ────────────────────────────────────────────────────────


async def test_list_articles_pagination(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    for i in range(5):
        await _insert_article(
            session_factory,
            title=f"art{i}",
            published_offset_minutes=i,  # i 越大越旧
        )

    r = await client.get("/api/v1/articles", params={"page": 2, "size": 2})
    data = r.json()
    assert data["page"] == 2
    assert data["size"] == 2
    assert data["total"] == 5
    assert len(data["items"]) == 2
    titles = [it["title"] for it in data["items"]]
    # 全列按 published_at DESC: art0 (最新) → art1 → art2 → art3 → art4
    # page=2 size=2 拿 2-3 条 (0-based) = art2, art3
    assert titles == ["art2", "art3"]


# ─── 8. 列表: topic 折叠 (child 不出现) ──────────────────────────────────────


async def test_list_articles_excludes_topic_children(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    parent = await _insert_article(session_factory, title="主文")
    child1 = await _insert_article(session_factory, title="转发 1")
    child2 = await _insert_article(session_factory, title="转发 2")
    await _insert_article(session_factory, title="独立文")
    await _link_child(session_factory, parent_id=parent, child_id=child1)
    await _link_child(session_factory, parent_id=parent, child_id=child2)

    r = await client.get("/api/v1/articles")
    titles = [it["title"] for it in r.json()["items"]]
    assert r.json()["total"] == 2
    assert "主文" in titles
    assert "独立文" in titles
    assert "转发 1" not in titles
    assert "转发 2" not in titles


# ─── 9. 详情: parent → 含 related_articles ────────────────────────────────


async def test_get_article_detail_returns_related_children(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    parent = await _insert_article(session_factory, title="腾讯 Q3 财报", market="HK")
    c1 = await _insert_article(session_factory, title="转发 - 财新")
    c2 = await _insert_article(session_factory, title="转发 - 36 氪")
    await _link_child(session_factory, parent_id=parent, child_id=c1)
    await _link_child(session_factory, parent_id=parent, child_id=c2)

    r = await client.get(f"/api/v1/articles/{parent}")
    assert r.status_code == 200
    data = r.json()
    assert data["title"] == "腾讯 Q3 财报"
    assert len(data["related_articles"]) == 2
    related_titles = {it["title"] for it in data["related_articles"]}
    assert related_titles == {"转发 - 财新", "转发 - 36 氪"}


# ─── 10. 详情: child → 重定向到 parent ────────────────────────────────────


async def test_get_article_detail_child_redirects_to_parent(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    parent = await _insert_article(session_factory, title="主文 P")
    child = await _insert_article(session_factory, title="子文 C")
    await _link_child(session_factory, parent_id=parent, child_id=child)

    r = await client.get(f"/api/v1/articles/{child}")
    assert r.status_code == 200
    data = r.json()
    # 用户分享了 child URL, 详情页展示主文 + child 列表
    assert data["title"] == "主文 P"
    assert data["article_id"] == str(parent)
    assert any(it["title"] == "子文 C" for it in data["related_articles"])


# ─── 11. 详情: 不存在的合法 UUID → 404 ────────────────────────────────────


async def test_get_article_detail_404_when_not_found(
    client: httpx.AsyncClient,
) -> None:
    fake_uuid = uuid.uuid4()
    r = await client.get(f"/api/v1/articles/{fake_uuid}")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "article_not_found"


# ─── 12. 详情: 非法 UUID 字符串 → 404 (不是 500) ──────────────────────────


async def test_get_article_detail_404_when_invalid_uuid(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/api/v1/articles/not-a-uuid")
    assert r.status_code == 404
    # 防御性: 不能因 UUID 解析失败 raise 500


# ─── 13. 搜索: 中文 query "招股" ──────────────────────────────────────────


async def test_search_articles_chinese_query(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _insert_article(
        session_factory,
        title="腾讯递交招股说明书",
        summary="腾讯于 2026 年向港交所递交招股说明书",
    )
    await _insert_article(
        session_factory,
        title="阿里巴巴回港",
        summary="阿里巴巴回港二次上市",
    )

    r = await client.get("/api/v1/search/articles", params={"q": "招股"})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "腾讯递交招股说明书"
    assert data["query"] == "招股"
    assert data["items"][0]["rank"] > 0


# ─── 14. 搜索: 英文 query ─────────────────────────────────────────────────


async def test_search_articles_english_query(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _insert_article(
        session_factory,
        title="HKEX subscription open",
        summary="HKEX IPO subscription window opens this Friday",
    )
    await _insert_article(
        session_factory, title="Other news", summary="Hello world"
    )

    r = await client.get(
        "/api/v1/search/articles", params={"q": "subscription"}
    )
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 1
    assert "subscription" in data["items"][0]["summary"]


# ─── 15. 搜索: 空 q (路由层校验) ──────────────────────────────────────────


async def test_search_articles_empty_query_is_422(
    client: httpx.AsyncClient,
) -> None:
    r = await client.get("/api/v1/search/articles", params={"q": ""})
    # Query(min_length=1) → 422
    assert r.status_code == 422


# ─── 16. 搜索: 全标点 → 200 + items=[] ────────────────────────────────────


async def test_search_articles_all_punct_returns_empty(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _insert_article(session_factory, title="正常文章", summary="腾讯")

    r = await client.get("/api/v1/search/articles", params={"q": "!!!,,,"})
    assert r.status_code == 200
    # PG plainto_tsquery 把全标点解析成空, 不会命中任何文章
    assert r.json()["total"] == 0


# ─── 17. 缓存命中: 二次调用不打 DB ────────────────────────────────────────


async def test_list_articles_cache_hit_avoids_db_call(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """二次相同参数调用走 Redis 缓存, ``_list_articles_db`` 不被再次调."""
    await _insert_article(session_factory, title="A")
    await _insert_article(session_factory, title="B")

    # 首次正常调用 → 写缓存
    r1 = await client.get(
        "/api/v1/articles", params={"market": "HK", "page": 1, "size": 20}
    )
    assert r1.status_code == 200
    first_count = r1.json()["total"]

    # 替换 _list_articles_db 让它必抛, 二次相同参数应走缓存绕过它
    real_db_fn = article_service._list_articles_db
    db_calls: list[int] = []

    async def boom(*args: Any, **kwargs: Any) -> Any:
        db_calls.append(1)
        raise RuntimeError("不该被调到 — cache 应命中")

    monkeypatch.setattr(article_service, "_list_articles_db", boom)
    try:
        r2 = await client.get(
            "/api/v1/articles", params={"market": "HK", "page": 1, "size": 20}
        )
        assert r2.status_code == 200
        assert r2.json()["total"] == first_count
        assert db_calls == [], "cache 命中后 _list_articles_db 不应被调"
    finally:
        monkeypatch.setattr(article_service, "_list_articles_db", real_db_fn)


# ─── 18. invalidate_namespace 主动清缓存 ──────────────────────────────────


async def test_invalidate_namespace_drops_list_cache(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``invalidate_namespace('articles:list')`` 后再调列表应回源 DB.

    模拟 ``article_ingest.dispatcher`` 写入新文章后清缓存的真实场景.
    """
    await _insert_article(session_factory, title="X")

    r1 = await client.get("/api/v1/articles", params={"market": "HK"})
    assert r1.json()["total"] == 1

    # 后台 ingest 又入了一篇新文 + 调 invalidate
    await _insert_article(session_factory, title="Y")
    cleared = await invalidate_namespace("articles:list", "articles:detail")
    assert cleared >= 1

    r2 = await client.get("/api/v1/articles", params={"market": "HK"})
    # cache 失效 → 回源 DB → 看到新文章
    assert r2.json()["total"] == 2
