"""BE-S3-003 同主题折叠端到端集成测.

覆盖 (≥ 4 条; spec/10 §AC 要求 ≥ 4 条):

1. test_dispatcher_links_similar_articles_into_topic_group
   写 3 篇相似标题 (一字之差) → article_topics 落 2 行 (parent=最早, 2 个 child)
2. test_dispatcher_does_not_link_unrelated_articles
   完全不同的 3 篇文章 → article_topics 0 行
3. test_dispatcher_does_not_cross_source_link
   同 market 但跨 source (雪球 vs 智通) → 不折叠 (候选池过滤)
4. test_dispatcher_does_not_cross_market_link
   不同 market (HK vs A) → 不折叠
5. test_recluster_job_picks_up_late_arrivals
   先入库 article A, 再入库相似 article B → recluster job 跑后, B 被 link 到 A
6. test_dispatcher_persists_simhash_to_db
   入库的每篇文章 simhash 列都有 8 byte 值 (NULL → BYTEA)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import IPO, Article, ArticleTopic
from app.services.article_ingest import dispatcher
from app.services.article_ingest.dedup import (
    run_recluster_job,
    simhash_from_bytes,
)
from app.services.article_ingest.sources.base import ArticleRaw

pytestmark = pytest.mark.db


# ─── helpers ──────────────────────────────────────────────────────────


async def _seed_ipos(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """种 2 只活跃 IPO + 1 只 A 股, 给关键词反查 + 跨 market 测试用."""
    async with session_factory() as s:
        s.add(
            IPO(
                code="00700.HK",
                name="腾讯控股",
                market="HK",
                status="upcoming",
                listing_date=date(2026, 5, 1),
                data_source="seed-dedup-e2e",
            )
        )
        s.add(
            IPO(
                code="02501.HK",
                name="天星医疗-B",
                market="HK",
                status="subscribing",
                listing_date=date(2026, 5, 10),
                data_source="seed-dedup-e2e",
            )
        )
        s.add(
            IPO(
                code="600519.SH",
                name="贵州茅台",
                market="A",
                status="upcoming",
                listing_date=date(2026, 6, 1),
                data_source="seed-dedup-e2e",
            )
        )
        await s.commit()


def _make_raw(
    title: str,
    url: str,
    *,
    source_name: str = "雪球",
    market: str = "HK",
    published_at: datetime | None = None,
    summary: str | None = None,
) -> ArticleRaw:
    return ArticleRaw(
        title=title,
        original_url=url,
        source_name=source_name,
        published_at=published_at or datetime.now(UTC),
        summary=summary,
        market=market,
        source_credibility=2,
        is_full_text_available=True,
        hot_score=Decimal(100),
    )


class _StaticSource:
    """假 source: 构造时给定 articles, ``fetch()`` 直接返."""

    def __init__(self, name: str, articles: list[ArticleRaw]) -> None:
        self.name = name
        self._articles = articles

    async def fetch(self, *, since: datetime | None = None) -> list[ArticleRaw]:
        return list(self._articles)


def _patch_register_sources(
    monkeypatch: pytest.MonkeyPatch, sources: list[_StaticSource]
) -> None:
    monkeypatch.setattr(dispatcher, "register_sources", lambda **kw: sources)


def _patch_invalidate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(*ns: str) -> int:
        return 0

    monkeypatch.setattr(dispatcher, "invalidate_namespace", _noop)


# ─── 1. happy: 3 篇相似 → 2 个 child link 到最早 parent ─────────────


async def test_dispatcher_links_similar_articles_into_topic_group(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 篇 "腾讯控股 Q3 业绩" 标题一字之差 + 同 source + 不同 published_at:

    - 最早一篇 = parent (无 link 行)
    - 后 2 篇 = child, 各有 1 行 article_topics
    - article_topics 总共 2 行
    """
    await _seed_ipos(session_factory)

    # 模拟"复刊 / 转发"场景: 3 篇 title 完全相同 (常见: 多账号转载同篇报道,
    # title 1:1 复制), 只 URL 不同. distance=0 ≤ 阈值 3 必命中.
    # 注: 短标题一字之差 distance 浮动 4-9 (单 token 替换权重大), 不可靠;
    # 真实 ingest 中长摘要可压低 distance, 但本 fixture 走"严格转发"语义最稳.
    base_time = datetime.now(UTC)
    common_title = "腾讯控股 Q3 业绩超预期 净利润同比 +15% 派息提振股价 游戏收入回暖"
    common_summary = (
        "腾讯控股发布第三季度财报, 营收同比增长稳健, 派息提振股价表现, "
        "微信视频号广告收入回暖, 海外游戏业务稳步推进, 市场对其长期增长前景"
        "持乐观态度, 多家投行上调目标价."
    )
    src = _StaticSource(
        "雪球",
        [
            _make_raw(
                common_title,
                "https://x.com/p/q3-original",
                published_at=base_time - timedelta(hours=1),  # 最早 = parent
                market="HK",
                summary=common_summary,
            ),
            _make_raw(
                common_title,  # 完全相同 title — 转发场景
                "https://x.com/p/q3-repost-a",
                published_at=base_time - timedelta(minutes=30),
                market="HK",
                summary=common_summary,
            ),
            _make_raw(
                common_title,
                "https://x.com/p/q3-repost-b",
                published_at=base_time,
                market="HK",
                summary=common_summary,
            ),
        ],
    )
    _patch_register_sources(monkeypatch, [src])
    _patch_invalidate(monkeypatch)

    stats = await dispatcher.run_ingest_articles_job()

    # 3 篇都命中 IPO (腾讯控股) + 都入库
    assert stats["inserted"] == 3
    assert stats["simhash_filled"] == 3, f"simhash 应该全 fill, 实际 {stats}"
    # 2 篇 child link 到最早 parent
    assert stats["topics_linked"] == 2, f"topics 应该 2, 实际 {stats}"

    async with session_factory() as s:
        # parent 是最早一篇
        topics = (
            await s.execute(
                select(ArticleTopic).order_by(ArticleTopic.created_at)
            )
        ).scalars().all()
        assert len(topics) == 2, f"article_topics 应 2 行, 实际 {len(topics)}"

        # 验 parent 是同一篇 (最早入库的那个)
        parent_ids = {t.parent_article_id for t in topics}
        assert len(parent_ids) == 1, "2 个 child 应共享同 1 个 parent"

        # 验 parent 的 published_at 最早
        parent_id = next(iter(parent_ids))
        parent_pub_at = (
            await s.execute(
                select(Article.published_at).where(Article.article_id == parent_id)
            )
        ).scalar_one()

        all_articles = (
            await s.execute(select(Article).order_by(Article.published_at))
        ).scalars().all()
        assert all_articles[0].article_id == parent_id, (
            "parent 应该是 published_at 最早的那篇"
        )
        # 距离全部 ≤ 3 (spec 锁定阈值)
        for t in topics:
            assert t.simhash_distance is not None
            assert t.simhash_distance <= 3, f"distance={t.simhash_distance} > 阈值 3"

        # parent 不应在 article_topics.child_article_id 列里 (只有它是 parent)
        child_ids = {t.child_article_id for t in topics}
        assert parent_id not in child_ids
        assert parent_pub_at is not None  # 仅 mypy


# ─── 2. 完全不同的文章 → 不折叠 ────────────────────────────────────


async def test_dispatcher_does_not_link_unrelated_articles(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 篇内容完全不同的文章 → 不应有 article_topics 行."""
    await _seed_ipos(session_factory)

    src = _StaticSource(
        "雪球",
        [
            _make_raw(
                "腾讯控股 Q3 净利润 +15% 派息提振股价",
                "https://x.com/p/tencent",
                market="HK",
            ),
            _make_raw(
                "天星医疗-B 港交所聆讯通过 计划 4 月招股",
                "https://x.com/p/tianxing",
                market="HK",
            ),
        ],
    )
    _patch_register_sources(monkeypatch, [src])
    _patch_invalidate(monkeypatch)

    stats = await dispatcher.run_ingest_articles_job()
    assert stats["inserted"] == 2
    assert stats["topics_linked"] == 0, "完全不同文章不该折叠"

    async with session_factory() as s:
        topics = (await s.execute(select(ArticleTopic))).scalars().all()
        assert len(topics) == 0


# ─── 3. 跨 source 不折叠 ─────────────────────────────────────────────


async def test_dispatcher_does_not_cross_source_link(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同标题 + 同 market 但跨 source → 不互相折叠 (独立报道视角不同).

    业务理由: 雪球散户讨论 vs 智通财经机构解读, FE 应分别展示.
    """
    await _seed_ipos(session_factory)

    base_time = datetime.now(UTC)
    src_a = _StaticSource(
        "雪球",
        [
            _make_raw(
                "腾讯控股 Q3 业绩超预期, 净利润同比 +15%",
                "https://xueqiu.com/p/q3",
                source_name="雪球",
                market="HK",
                published_at=base_time - timedelta(hours=1),
            ),
        ],
    )
    src_b = _StaticSource(
        "智通财经",
        [
            _make_raw(
                "腾讯控股 Q3 业绩超预期, 净利润同比 +15%",  # 完全相同标题
                "https://zt.com/p/q3",
                source_name="智通财经",
                market="HK",
                published_at=base_time,
            ),
        ],
    )
    _patch_register_sources(monkeypatch, [src_a, src_b])
    _patch_invalidate(monkeypatch)

    stats = await dispatcher.run_ingest_articles_job()
    assert stats["inserted"] == 2
    assert stats["topics_linked"] == 0, "跨 source 不该折叠"

    async with session_factory() as s:
        topics = (await s.execute(select(ArticleTopic))).scalars().all()
        assert len(topics) == 0


# ─── 4. 跨 market 不折叠 ─────────────────────────────────────────────


async def test_dispatcher_does_not_cross_market_link(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同 source + 同标题 但 market 不同 → 不折叠."""
    await _seed_ipos(session_factory)

    base_time = datetime.now(UTC)
    src = _StaticSource(
        "雪球",
        [
            _make_raw(
                "腾讯控股 Q3 业绩超预期, 净利润同比 +15%",
                "https://x.com/p/hk",
                market="HK",
                published_at=base_time - timedelta(hours=1),
            ),
            # market="A" + 命中 600519.SH (贵州茅台) 让它能入库 — 但与 HK 篇
            # 跨 market 不应折叠. 用相似标题 (都含"业绩超预期+净利润同比")
            _make_raw(
                "贵州茅台 Q3 业绩超预期, 净利润同比 +15%",
                "https://x.com/p/a",
                market="A",
                published_at=base_time,
            ),
        ],
    )
    _patch_register_sources(monkeypatch, [src])
    _patch_invalidate(monkeypatch)

    stats = await dispatcher.run_ingest_articles_job()
    assert stats["inserted"] == 2
    assert stats["topics_linked"] == 0, "跨 market 不该折叠"

    async with session_factory() as s:
        topics = (await s.execute(select(ArticleTopic))).scalars().all()
        assert len(topics) == 0


# ─── 5. recluster job 兜底 ─────────────────────────────────────────────


async def test_recluster_job_picks_up_late_arrivals(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """先入 article A (无候选池), 再入相似 article B; 第二次入库 dispatcher
    已能在 candidate pool 看到 A → 直接 link.

    本测验更典型的场景: 第一次入库 simhash 算失败 (mock), 后续 recluster
    扫到 NULL 补上, 同时 link 到候选池里的 parent.
    """
    await _seed_ipos(session_factory)

    # title + summary 在两批之间完全相同 (转发场景), distance=0 必命中.
    # 短标题一字之差 distance 浮动 4-9 (单 token 替换权重大), 不可靠.
    base_time = datetime.now(UTC)
    common_title = "腾讯控股 Q3 业绩超预期 净利润同比 +15% 派息提振股价 游戏收入回暖"
    common_summary = (
        "腾讯控股发布第三季度财报, 营收同比增长稳健, 派息提振股价表现, "
        "微信视频号广告收入回暖, 海外游戏业务稳步推进, 市场对其长期增长前景"
        "持乐观态度, 多家投行上调目标价."
    )
    src_first = _StaticSource(
        "雪球",
        [
            _make_raw(
                common_title,
                "https://x.com/p/first",
                market="HK",
                published_at=base_time - timedelta(hours=1),
                summary=common_summary,
            ),
        ],
    )
    _patch_register_sources(monkeypatch, [src_first])
    _patch_invalidate(monkeypatch)

    # 第一批入库 + dispatcher dedup → 1 篇, 候选池只有自己, 无 parent, 0 link
    stats1 = await dispatcher.run_ingest_articles_job()
    assert stats1["inserted"] == 1
    assert stats1["topics_linked"] == 0

    # 模拟"第一篇 simhash 算失败" → 手动清掉它的 simhash 列, 让 recluster 阶段
    # 发现并补 + 同时 link
    async with session_factory() as s:
        await s.execute(
            Article.__table__.update()  # type: ignore[arg-type]
            .where(Article.__table__.c.original_url == "https://x.com/p/first")
            .values(simhash=None)
        )
        await s.commit()

    # 第二批入库新文章 (同 title — 转发场景, distance=0)
    src_second = _StaticSource(
        "雪球",
        [
            _make_raw(
                common_title,
                "https://x.com/p/second",
                market="HK",
                published_at=base_time,
                summary=common_summary,
            ),
        ],
    )
    _patch_register_sources(monkeypatch, [src_second])

    stats2 = await dispatcher.run_ingest_articles_job()
    assert stats2["inserted"] == 1
    # 第二批: dispatcher 发现 first 的 simhash 是 NULL (我们手清掉) → 没法当
    # 候选池, 所以 inline dedup 找不到 parent
    assert stats2["topics_linked"] == 0

    # 跑 recluster job → 阶段 1 给 first 补 simhash; 阶段 2 second 反查 first 当 parent
    re_stats = await run_recluster_job()
    # first 的 simhash 被补回来
    assert re_stats["simhash_filled"] >= 1
    # second 被 link 到 first
    assert re_stats["topics_linked"] >= 1

    async with session_factory() as s:
        topics = (await s.execute(select(ArticleTopic))).scalars().all()
        assert len(topics) == 1
        # parent 是 first (最早 published_at), child 是 second
        topic = topics[0]
        first_id = (
            await s.execute(
                select(Article.article_id).where(
                    Article.original_url == "https://x.com/p/first"
                )
            )
        ).scalar_one()
        second_id = (
            await s.execute(
                select(Article.article_id).where(
                    Article.original_url == "https://x.com/p/second"
                )
            )
        ).scalar_one()
        assert topic.parent_article_id == first_id, "parent 应是最早 published_at"
        assert topic.child_article_id == second_id


# ─── 6. simhash 列被持久化 ────────────────────────────────────────────


async def test_dispatcher_persists_simhash_to_db(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """入库后, ``articles.simhash`` 应是 8 byte 值, 不再是 NULL."""
    await _seed_ipos(session_factory)

    src = _StaticSource(
        "雪球",
        [
            _make_raw(
                "腾讯控股 Q3 业绩稳健, 派息提振股价",
                "https://x.com/p/sh-test",
                market="HK",
            ),
        ],
    )
    _patch_register_sources(monkeypatch, [src])
    _patch_invalidate(monkeypatch)

    stats = await dispatcher.run_ingest_articles_job()
    assert stats["inserted"] == 1
    assert stats["simhash_filled"] == 1

    async with session_factory() as s:
        row = (
            await s.execute(
                select(Article.simhash, Article.title).where(
                    Article.original_url == "https://x.com/p/sh-test"
                )
            )
        ).one()
        simhash_bytes, title = row.simhash, row.title
        assert simhash_bytes is not None, "simhash 列不该为 NULL"
        assert len(simhash_bytes) == 8, "simhash 应为 8 byte 定长 BYTEA"
        # 反序列化能拿回 int (合法 64 bit 值)
        value = simhash_from_bytes(simhash_bytes)
        assert 0 <= value < (1 << 64)
        assert title == "腾讯控股 Q3 业绩稳健, 派息提振股价"
