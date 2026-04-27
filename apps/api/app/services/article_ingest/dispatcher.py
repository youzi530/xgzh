"""文章 ingest 调度器 (BE-S3-002).

总入口 ``run_ingest_articles_job`` 由 APScheduler 调用; 不抛异常, 失败一律
``logger.exception`` 后返 stats (与 ``ipo_ingest_service.run_ingest_a_job``
风格一致, 防 scheduler 把 job 标 failed 后停掉).

执行流
======
1. 自己开 session: scheduler 不在请求作用域内, 不能用 ``Depends(get_session)``
2. 从 ``ipos`` 表查活跃 IPO → ``IPOKeywordIndex`` (反查文章里命中哪些 IPO)
3. 注册 sources (XueqiuClient + ZhitongRSSClient + 后续可加), 每源独立 ``await`` —
   单源失败 ``logger.warning`` skip, 不影响其它源 (sources 自己已 fail-soft)
4. 关键词匹配: 命中 ≥ 1 个 IPO → 写 related_ipos; 命中 0 → 丢弃
   (MVP: 不存"无关 IPO 的财经新闻", 否则数据池被噪音淹没)
5. 写库走 ``INSERT ... ON CONFLICT (original_url) DO NOTHING`` 实现幂等 ingest

为什么 source 注册在 ``register_sources`` 而非 module-level
==========================================================
sources 实例化时要读 settings + 关键词集 (动态), 不能在 import 时构造.
注册函数本身又故意接 ``IPOKeywordIndex`` 参数, 让单测可以传 mock index.

为什么 ``upsert_articles`` 不返 ``inserted`` 精确计数
======================================================
``ON CONFLICT DO NOTHING`` 的精确 inserted 计数要先 ``SELECT existing keys``
再走 ``IN`` 比较 (与 ``ipo_ingest_service.upsert_ipos`` 同款做法), 但 articles
表的 ``original_url`` 是 Text 列, IN 子句包 100+ URL 的 SQL 会爆 8KB 单 query
长度限制; 改成 ``RETURNING article_id`` 走 ``len(returned)`` 算 inserted, 是
PG 标准玩法.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import invalidate_namespace
from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import IPO, Article
from app.services.article_ingest.sources.base import (
    ArticleRaw,
    ArticleSource,
    IPOKeywordIndex,
)
from app.services.article_ingest.sources.xueqiu_client import XueqiuClient
from app.services.article_ingest.sources.zhitong_rss_client import ZhitongRSSClient

# 写库时 batch insert 的最大 row 数; PG ``INSERT ... VALUES (...)`` 无硬性
# 上限, 但单条 SQL > 1MB 后 driver 会变慢. 200 行约 100KB SQL, 适配大多 ingest.
_INSERT_BATCH_SIZE: Final[int] = 200


def _article_raw_to_row(art: ArticleRaw) -> dict[str, Any]:
    """``ArticleRaw`` (frozen dataclass) → ``articles`` 表 INSERT row dict.

    手写 mapping 不走 ``dataclasses.asdict``: 字段名虽然全 1:1 对齐, 但 ``hot_score``
    / ``source_credibility`` 类型 (Decimal / int) 显式列出阅读性更好. 与
    ``_ipo_item_to_row`` (BE-S2-000) 风格一致.
    """
    return {
        "title": art.title,
        "summary": art.summary,
        "source_name": art.source_name,
        "source_logo_url": art.source_logo_url,
        "source_credibility": art.source_credibility,
        "original_url": art.original_url,
        "market": art.market,
        "related_ipos": art.related_ipos,
        # sentiment / sentiment_score / keywords / simhash / summary 留 NULL
        # (BE-S3-003/004/005 后置补)
        "hot_score": art.hot_score,
        "is_full_text_available": art.is_full_text_available,
        "published_at": art.published_at,
        # fetched_at: 用 server_default now() 由 PG 直接填, row 里不传
    }


async def upsert_articles(
    session: AsyncSession, articles: list[ArticleRaw]
) -> dict[str, int]:
    """批量写 ``ArticleRaw`` 进 ``articles`` 表; ``ON CONFLICT DO NOTHING`` 幂等.

    返回 ``{"received": n, "inserted": x, "skipped": y}``:
    - ``inserted`` = 真正新增的行数 (走 ``RETURNING article_id`` 计数)
    - ``skipped`` = ``original_url`` UNIQUE 命中已存在的 (重复抓取)

    分批 ``_INSERT_BATCH_SIZE`` 提交, 让单 SQL 不超 1MB; 失败一批不影响其它批
    (try/except 包外层时调用方处理事务).
    """
    if not articles:
        return {"received": 0, "inserted": 0, "skipped": 0}

    rows = [_article_raw_to_row(a) for a in articles]
    inserted = 0

    for i in range(0, len(rows), _INSERT_BATCH_SIZE):
        batch = rows[i : i + _INSERT_BATCH_SIZE]
        stmt = (
            pg_insert(Article.__table__)  # type: ignore[arg-type]
            .values(batch)
            .on_conflict_do_nothing(index_elements=["original_url"])
            .returning(Article.__table__.c.article_id)
        )
        result = await session.execute(stmt)
        inserted += len(result.fetchall())

    await session.flush()

    return {
        "received": len(rows),
        "inserted": inserted,
        "skipped": len(rows) - inserted,
    }


async def _load_ipo_keyword_index(session: AsyncSession) -> IPOKeywordIndex:
    """从 ``ipos`` 表查活跃 IPO → ``IPOKeywordIndex``.

    "活跃" 定义: 上市 ≤ 90d 内的 + 在申购 / 待申购 + 申请阶段 (HK 占位).
    spec/03 §1 给的 "近期 IPO" 范畴.

    用 raw SQL ``data_source != 'seed'`` 过滤掉 hkex_client cold-start seed,
    防止索引被几条静态样例污染.
    """
    q = select(IPO.code, IPO.market, IPO.name).where(
        sa_text(
            "(status IN ('upcoming', 'subscribing', 'pricing', 'pending') "
            "OR (status = 'listed' AND listing_date >= now() - interval '90 days') "
            "OR code LIKE 'AP%')"
        )
    )
    rows = (await session.execute(q)).all()
    return IPOKeywordIndex.from_rows([(r.code, r.market, r.name) for r in rows])


def register_sources(
    *,
    settings: Settings,
    keyword_index: IPOKeywordIndex,
) -> list[ArticleSource]:
    """实例化所有数据源.

    顺序无所谓 (各源并发时间互相不影响); 传 ``keyword_index`` 是给 XueqiuClient
    用 — 它需要关键词列表去打雪球搜索 API. 智通 RSS 不需要 (RSS 自带"全部最近
    新闻", 关键词反查由 dispatcher 阶段统一做).
    """
    queries: list[str] = []
    seen: set[str] = set()
    for ipo in keyword_index._ipos:  # noqa: SLF001 — internal access by design
        # 取每只 IPO 的"主关键词" (name 全名, name 短名) 各一份, code 不入查询
        # (雪球 API 对纯数字 code 召回率差, 名字命中率高)
        for kw in ipo.keywords:
            if kw and not kw[0].isdigit() and kw not in seen:
                seen.add(kw)
                queries.append(kw)
    # 避免一次查询太多 (雪球 API 单 ingest 走 N 次), 截取 top 限额
    queries = queries[: settings.article_ingest_xueqiu_max_queries]

    sources: list[ArticleSource] = [
        ZhitongRSSClient(settings=settings),
    ]
    if queries:
        sources.append(XueqiuClient(settings=settings, queries=queries))
    return sources


async def _fetch_one_source(
    source: ArticleSource, *, since: datetime | None
) -> list[ArticleRaw]:
    """单源 fetch wrapper: source 内部已 fail-soft, 这里再补一层防御.

    返回 ``[]`` 不抛 — dispatcher 主循环不带 try/except, 全靠这里兜.
    """
    try:
        return await source.fetch(since=since)
    except Exception as e:  # noqa: BLE001 — fail-soft per source
        logger.exception(f"article_ingest.source_failed name={source.name}: {e}")
        return []


async def run_ingest_articles_job(
    settings: Settings | None = None,
) -> dict[str, int]:
    """APScheduler 回调入口: 从所有数据源抓取最近文章 → 关键词匹配 → upsert.

    设计:
    - 不抛异常: 任何失败 (网络 / DB / 解析) 都 logger.exception 后返 stats,
      防止 scheduler 把整个 job 标 failed 后停掉
    - 自己开 session: 调度器不在请求作用域内
    - 关键词匹配 0 → 丢弃; 命中 ≥ 1 → 写 related_ipos
    - 写完调 ``invalidate_namespace`` 让 BE-S3-006 文章列表 / 详情缓存立即回源
    """
    settings = settings or get_settings()
    stats: dict[str, int] = {
        "sources": 0,
        "fetched": 0,
        "matched": 0,  # 关键词反查 ≥ 1 个 IPO 的文章数
        "received": 0,  # 进入写库流程
        "inserted": 0,
        "skipped": 0,
        "errors": 0,
        "cache_invalidated": 0,
    }

    factory = get_session_factory()
    try:
        async with factory() as session:
            keyword_index = await _load_ipo_keyword_index(session)
    except Exception as e:
        logger.exception(f"article_ingest.load_keyword_index_failed: {e}")
        stats["errors"] = 1
        return stats

    if len(keyword_index) == 0:
        logger.warning(
            "article_ingest.skipped — IPO keyword index empty "
            "(ipos table has no active rows)"
        )
        return stats

    sources = register_sources(settings=settings, keyword_index=keyword_index)
    stats["sources"] = len(sources)

    all_fetched: list[ArticleRaw] = []
    for src in sources:
        items = await _fetch_one_source(src, since=None)
        all_fetched.extend(items)
        logger.info(
            f"article_ingest.source_done name={src.name} fetched={len(items)}"
        )
    stats["fetched"] = len(all_fetched)

    # 关键词匹配 + 写 related_ipos
    matched: list[ArticleRaw] = []
    for art in all_fetched:
        hits = keyword_index.match(title=art.title, summary=art.summary)
        if not hits:
            continue
        # 用 dataclass.replace 不动原对象 (frozen=True)
        from dataclasses import replace

        matched.append(replace(art, related_ipos=hits))
    stats["matched"] = len(matched)
    stats["received"] = len(matched)

    if not matched:
        logger.info(
            f"article_ingest.no_match fetched={stats['fetched']} sources={stats['sources']}"
        )
        return stats

    try:
        async with factory() as session:
            db_stats = await upsert_articles(session, matched)
            await session.commit()
        stats["inserted"] = db_stats["inserted"]
        stats["skipped"] = db_stats["skipped"]
    except Exception as e:
        logger.exception(
            f"article_ingest.upsert_failed received={stats['received']}: {e}"
        )
        stats["errors"] = 1
        return stats

    # 失效 BE-S3-006 文章列表 / 详情缓存; 失败已被 invalidate_namespace 内部 catch
    stats["cache_invalidated"] = await invalidate_namespace(
        "articles:list", "articles:detail"
    )

    logger.info(
        f"article_ingest.ok sources={stats['sources']} fetched={stats['fetched']} "
        f"matched={stats['matched']} inserted={stats['inserted']} "
        f"skipped={stats['skipped']} cache_invalidated={stats['cache_invalidated']}"
    )
    return stats


__all__ = [
    "register_sources",
    "run_ingest_articles_job",
    "upsert_articles",
]
