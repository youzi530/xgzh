"""BE-S3-002: 文章多源 ingest 端到端集成测.

覆盖 (≥ 4 条; spec/10 §AC 要求 ≥ 4 条):

1. test_dispatcher_writes_matched_articles_to_db
   mock 2 个 source 各返 5 条, IPO 关键词命中 4 条 → DB 落 4 行 (ON CONFLICT 路径)
2. test_dispatcher_idempotent_second_run_no_new_rows
   重跑同样数据 → DB 行数不变, ``inserted=0`` ``skipped=已抓数``
3. test_dispatcher_skips_articles_without_ipo_match
   无 IPO 关键词命中的文章 → 全部丢弃, DB 0 行
4. test_dispatcher_one_source_failure_does_not_break_others
   source A raise → source B 正常入库 (fail-soft 策略)
5. test_dispatcher_skipped_when_ipo_table_empty
   ``ipos`` 表无活跃 IPO → 整个 ingest 早退, 不抓 source

不验:
- 雪球 / 智通 真实 HTTP (单测覆盖)
- simhash 入库 / sentiment 标注 (BE-S3-003 / 004 后置)
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import IPO, Article
from app.services.article_ingest import dispatcher
from app.services.article_ingest.sources.base import ArticleRaw, ArticleSource

pytestmark = pytest.mark.db


# ─── helper: 种 IPO + 构造假 source ─────────────────────────────────────


async def _seed_ipos(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """种 2 只活跃 IPO 给 ``IPOKeywordIndex`` 用."""
    async with session_factory() as s:
        s.add(
            IPO(
                code="00700.HK",
                name="腾讯控股",
                market="HK",
                status="upcoming",
                listing_date=date(2026, 5, 1),
                data_source="seed-ingest-e2e",
            )
        )
        s.add(
            IPO(
                code="02501.HK",
                name="天星医疗-B",
                market="HK",
                status="subscribing",
                listing_date=date(2026, 5, 10),
                data_source="seed-ingest-e2e",
            )
        )
        await s.commit()


def _make_raw(title: str, url: str, source_name: str = "雪球") -> ArticleRaw:
    return ArticleRaw(
        title=title,
        original_url=url,
        source_name=source_name,
        published_at=datetime.now(UTC),
        summary=None,
        market="BOTH",
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


class _RaisingSource:
    """假 source: ``fetch()`` 直接抛, 验 dispatcher 的 fail-soft."""

    name = "raising"

    async def fetch(self, *, since: datetime | None = None) -> list[ArticleRaw]:
        raise RuntimeError("network down")


def _patch_register_sources(
    monkeypatch: pytest.MonkeyPatch, sources: list[ArticleSource]
) -> None:
    """让 ``dispatcher.register_sources`` 返回我们的假 source 列表."""
    monkeypatch.setattr(dispatcher, "register_sources", lambda **kw: sources)


def _patch_invalidate(monkeypatch: pytest.MonkeyPatch) -> None:
    """关掉 cache invalidate (走 mock Redis 即可, 但减少噪音)."""

    async def _noop(*ns: str) -> int:
        return 0

    monkeypatch.setattr(dispatcher, "invalidate_namespace", _noop)


# ─── 1. happy: 2 source × 5 文章, 4 命中 IPO ───────────────────────────


async def test_dispatcher_writes_matched_articles_to_db(
    db_engine: AsyncEngine,  # noqa: ARG001
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001 — 启动 mock Redis
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2 source 共 10 条, 4 条 IPO 关键词命中 → DB 落 4 行, ``related_ipos`` 写对."""
    await _seed_ipos(session_factory)

    src_a = _StaticSource(
        "雪球",
        [
            _make_raw("腾讯控股 Q1 财报超预期", "https://xueqiu.com/p/q1tx"),
            _make_raw("天星医疗-B 招股书发布", "https://xueqiu.com/p/tsm-prospectus"),
            _make_raw("行业回顾 — 与 IPO 无关", "https://xueqiu.com/p/random_news_01"),
            _make_raw("美股科技股深度", "https://xueqiu.com/p/random_us"),
            _make_raw("天星医疗 路演纪要", "https://xueqiu.com/p/tsm-roadshow"),
        ],
    )
    src_b = _StaticSource(
        "智通财经",
        [
            _make_raw("天星医疗递交港交所申请", "https://zt.com/news/100", "智通财经"),
            _make_raw("行业资讯 — 与 IPO 无关", "https://zt.com/news/random_01", "智通财经"),
            _make_raw("市场综述 — 港股早评", "https://zt.com/news/morning", "智通财经"),
            _make_raw("商业地产分析", "https://zt.com/news/realestate", "智通财经"),
            _make_raw("欧洲央行政策评论", "https://zt.com/news/ecb", "智通财经"),
        ],
    )
    _patch_register_sources(monkeypatch, [src_a, src_b])
    _patch_invalidate(monkeypatch)

    stats = await dispatcher.run_ingest_articles_job()

    assert stats["sources"] == 2
    assert stats["fetched"] == 10
    # 命中: q1tx (腾讯控股), tsm-prospectus (天星医疗), tsm-roadshow (天星医疗),
    # zt/100 (天星医疗) → 4 条
    assert stats["matched"] == 4
    assert stats["received"] == 4
    assert stats["inserted"] == 4
    assert stats["skipped"] == 0
    assert stats["errors"] == 0

    async with session_factory() as s:
        result = await s.execute(select(Article).order_by(Article.title))
        rows = result.scalars().all()
        assert len(rows) == 4
        # related_ipos 写对了
        for row in rows:
            assert isinstance(row.related_ipos, list)
            assert len(row.related_ipos) >= 1
            codes = {ipo["code"] for ipo in row.related_ipos}
            assert codes & {"00700.HK", "02501.HK"}, (
                f"row {row.title} 没命中任何 IPO: {row.related_ipos}"
            )


# ─── 2. 幂等: 重跑 0 新增 ───────────────────────────────────────────────


async def test_dispatcher_idempotent_second_run_no_new_rows(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一批 source 数据跑两次 → 第二次 0 新增, ``skipped=N`` (ON CONFLICT 路径)."""
    await _seed_ipos(session_factory)

    src = _StaticSource(
        "雪球",
        [
            _make_raw("腾讯控股 Q1 业绩", "https://xueqiu.com/p/q1tx"),
            _make_raw("天星医疗 路演", "https://xueqiu.com/p/tsm-roadshow"),
        ],
    )
    _patch_register_sources(monkeypatch, [src])
    _patch_invalidate(monkeypatch)

    stats1 = await dispatcher.run_ingest_articles_job()
    assert stats1["inserted"] == 2

    # 第二跑 — 数据不变
    stats2 = await dispatcher.run_ingest_articles_job()
    assert stats2["fetched"] == 2
    assert stats2["matched"] == 2
    assert stats2["inserted"] == 0
    assert stats2["skipped"] == 2

    async with session_factory() as s:
        result = await s.execute(select(Article))
        assert len(result.scalars().all()) == 2


# ─── 3. 无 IPO 命中 → 全丢 ─────────────────────────────────────────────


async def test_dispatcher_skips_articles_without_ipo_match(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """source 返 3 条, 全部不命中 IPO → matched=0, DB 0 行."""
    await _seed_ipos(session_factory)

    src = _StaticSource(
        "雪球",
        [
            _make_raw("美联储加息分析", "https://x.com/p/fed"),
            _make_raw("欧洲债务危机回顾", "https://x.com/p/eu"),
            _make_raw("黄金价格走势", "https://x.com/p/gold"),
        ],
    )
    _patch_register_sources(monkeypatch, [src])
    _patch_invalidate(monkeypatch)

    stats = await dispatcher.run_ingest_articles_job()
    assert stats["fetched"] == 3
    assert stats["matched"] == 0
    assert stats["inserted"] == 0

    async with session_factory() as s:
        result = await s.execute(select(Article))
        assert result.scalars().all() == []


# ─── 4. fail-soft: 单源失败不影响其它源 ─────────────────────────────────


async def test_dispatcher_one_source_failure_does_not_break_others(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """source A 抛 → logger.exception + skip, source B 正常入库."""
    await _seed_ipos(session_factory)

    src_ok = _StaticSource(
        "雪球",
        [_make_raw("腾讯控股 Q1 业绩", "https://xueqiu.com/p/ok")],
    )
    src_fail = _RaisingSource()

    _patch_register_sources(monkeypatch, [src_fail, src_ok])
    _patch_invalidate(monkeypatch)

    stats = await dispatcher.run_ingest_articles_job()
    assert stats["sources"] == 2
    # 失败 source 返 [], 正常 source 返 1 → 总抓 1
    assert stats["fetched"] == 1
    assert stats["matched"] == 1
    assert stats["inserted"] == 1
    assert stats["errors"] == 0  # source 级别失败不计 errors

    async with session_factory() as s:
        result = await s.execute(select(Article))
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].original_url == "https://xueqiu.com/p/ok"


# ─── 5. 空 IPO 表 → 早退 ───────────────────────────────────────────────


async def test_dispatcher_skipped_when_ipo_table_empty(
    db_engine: AsyncEngine,
    patch_session_factory: None,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    redis_client: object,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ipos`` 表无活跃 IPO → keyword index 为空, 整个 ingest 不调任何 source."""
    # 不种 IPO; truncate_all 保 ipos 表空
    fail_if_called = _RaisingSource()
    _patch_register_sources(monkeypatch, [fail_if_called])
    _patch_invalidate(monkeypatch)

    stats = await dispatcher.run_ingest_articles_job()
    # 因为 keyword index 空, register_sources 都不会被调; sources=0
    assert stats["sources"] == 0
    assert stats["fetched"] == 0
    assert stats["matched"] == 0

    # DB 也保持空
    async with db_engine.connect() as conn:
        rows = await conn.execute(text("SELECT count(*) FROM articles"))
        assert rows.scalar_one() == 0
