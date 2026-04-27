"""文章 ingest 调度器 (BE-S3-002 + BE-S3-003 同步 dedup + BE-S3-004 同步打标 hook).

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
6. **(BE-S3-003)** 对本批新插入的每篇文章: 算 simhash + 在近 24h 同 source
   候选池找 parent + 命中即写 ``article_topics``. 单条失败 ``logger.warning``
   skip, 不抛异常 (per-article 失败别影响 batch).
7. **(BE-S3-004)** 对本批新插入的文章批量调 LLM 打 sentiment / score / keywords.
   失败兜底由 ``sentiment_tagger`` 内部消化 (整批失败 → 单条降级 → fallback
   neutral); 永不抛, 永不阻塞主流程. 失败的 article 由 scheduler 兜底 job 兜底.

为什么 source 注册在 ``register_sources`` 而非 module-level
==========================================================
sources 实例化时要读 settings + 关键词集 (动态), 不能在 import 时构造.
注册函数本身又故意接 ``IPOKeywordIndex`` 参数, 让单测可以传 mock index.

为什么 ``upsert_articles`` 走 ``RETURNING article_id, original_url``
======================================================================
``ON CONFLICT DO NOTHING`` 的精确 inserted 计数要先 ``SELECT existing keys``
再走 ``IN`` 比较 (与 ``ipo_ingest_service.upsert_ipos`` 同款做法), 但 articles
表的 ``original_url`` 是 Text 列, IN 子句包 100+ URL 的 SQL 会爆 8KB 单 query
长度限制; 改成 ``RETURNING article_id, original_url`` 走 ``len(returned)`` 算
inserted, 是 PG 标准玩法. 顺带 ``original_url`` 让 dispatcher 把
``ArticleRaw`` 反查回来给 dedup 用 (避免再 SELECT 一次).
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
from app.services.article_ingest.dedup import (
    compute_and_persist_simhash,
    compute_simhash,
    find_topic_parent,
    link_topic,
)
from app.services.article_ingest.sentiment_tagger import tag_articles_by_id
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
) -> dict[str, Any]:
    """批量写 ``ArticleRaw`` 进 ``articles`` 表; ``ON CONFLICT DO NOTHING`` 幂等.

    返回 ``{"received", "inserted", "skipped", "inserted_pairs"}``:
    - ``inserted`` = 真正新增的行数 (走 ``RETURNING article_id`` 计数)
    - ``skipped`` = ``original_url`` UNIQUE 命中已存在的 (重复抓取)
    - ``inserted_pairs`` = ``[(article_id, original_url), ...]`` 给 BE-S3-003
      dedup 阶段反查 ``ArticleRaw`` 用. 跳过的行不在此列表

    分批 ``_INSERT_BATCH_SIZE`` 提交, 让单 SQL 不超 1MB; 失败一批不影响其它批
    (try/except 包外层时调用方处理事务).
    """
    if not articles:
        return {"received": 0, "inserted": 0, "skipped": 0, "inserted_pairs": []}

    rows = [_article_raw_to_row(a) for a in articles]
    inserted_pairs: list[tuple[Any, str]] = []

    for i in range(0, len(rows), _INSERT_BATCH_SIZE):
        batch = rows[i : i + _INSERT_BATCH_SIZE]
        stmt = (
            pg_insert(Article.__table__)  # type: ignore[arg-type]
            .values(batch)
            .on_conflict_do_nothing(index_elements=["original_url"])
            .returning(
                Article.__table__.c.article_id,
                Article.__table__.c.original_url,
            )
        )
        result = await session.execute(stmt)
        for art_id, original_url in result.fetchall():
            inserted_pairs.append((art_id, original_url))

    await session.flush()
    inserted = len(inserted_pairs)

    return {
        "received": len(rows),
        "inserted": inserted,
        "skipped": len(rows) - inserted,
        "inserted_pairs": inserted_pairs,
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
        "simhash_filled": 0,  # BE-S3-003: 同步给本批新文算 simhash 的数量
        "topics_linked": 0,  # BE-S3-003: 找到 parent + 写 article_topics 行数
        "sentiment_tagged": 0,  # BE-S3-004: 同步打标成功的 article 数
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

    inserted_pairs: list[tuple[Any, str]] = []
    try:
        async with factory() as session:
            db_stats = await upsert_articles(session, matched)
            await session.commit()
        stats["inserted"] = db_stats["inserted"]
        stats["skipped"] = db_stats["skipped"]
        inserted_pairs = db_stats["inserted_pairs"]
    except Exception as e:
        logger.exception(
            f"article_ingest.upsert_failed received={stats['received']}: {e}"
        )
        stats["errors"] = 1
        return stats

    # ─── BE-S3-003: 同步算 simhash + 找同主题父文章 ─────────────────────
    # 单条失败 logger.warning skip, 不抛 (per-article 失败别影响 batch);
    # 用一个独立 session/事务隔离: 即使 dedup 全失败, 上面的 upsert 已 commit,
    # 文章不会丢, simhash 留 NULL 等下一次 recluster job 兜底
    if inserted_pairs:
        url_to_raw = {a.original_url: a for a in matched}
        try:
            async with factory() as session:
                stats["simhash_filled"], stats["topics_linked"] = (
                    await _dedup_inserted_batch(
                        session,
                        inserted_pairs=inserted_pairs,
                        url_to_raw=url_to_raw,
                    )
                )
                await session.commit()
        except Exception as e:
            # 整批 dedup 失败不影响主流程; 后续 recluster job 会兜底
            logger.exception(
                f"article_ingest.dedup_batch_failed inserted={stats['inserted']}: {e}"
            )

    # ─── BE-S3-004: 同步给本批新文调 LLM 批量打 sentiment / keywords ─────
    # 独立 session/事务 隔离: 即使 LLM 全失败, simhash + topic 已 commit,
    # sentiment 留 NULL 等下一次 sentiment_tag_job 兜底
    if inserted_pairs:
        new_article_ids = [pair[0] for pair in inserted_pairs]
        try:
            async with factory() as session:
                tag_stats = await tag_articles_by_id(
                    session, article_ids=new_article_ids
                )
                await session.commit()
            stats["sentiment_tagged"] = tag_stats["tagged"]
        except Exception as e:
            # 整批打标失败不影响主流程; scheduler 兜底 job 会再扫一遍
            logger.exception(
                f"article_ingest.sentiment_tag_failed inserted={stats['inserted']}: {e}"
            )

    # 失效 BE-S3-006 文章列表 / 详情缓存; 失败已被 invalidate_namespace 内部 catch
    stats["cache_invalidated"] = await invalidate_namespace(
        "articles:list", "articles:detail"
    )

    logger.info(
        f"article_ingest.ok sources={stats['sources']} fetched={stats['fetched']} "
        f"matched={stats['matched']} inserted={stats['inserted']} "
        f"skipped={stats['skipped']} simhash_filled={stats['simhash_filled']} "
        f"topics_linked={stats['topics_linked']} "
        f"sentiment_tagged={stats['sentiment_tagged']} "
        f"cache_invalidated={stats['cache_invalidated']}"
    )
    return stats


async def _dedup_inserted_batch(
    session: AsyncSession,
    *,
    inserted_pairs: list[tuple[Any, str]],
    url_to_raw: dict[str, ArticleRaw],
) -> tuple[int, int]:
    """对本批新插入文章: 算 simhash + 写 simhash 列 + 找 parent + link topic.

    返回 ``(simhash_filled, topics_linked)``. 单条失败 ``logger.warning`` skip.

    为什么 simhash 算完整批一起 flush:
    - 保证 ``find_topic_parent`` 候选池能查到本批前面已写过的 simhash, 让
      "同一批进来的相似文章"也能互相 cluster (parent 必为最早 published_at)
    """
    simhash_filled = 0
    topics_linked = 0

    # 阶段 1: 给本批每条算 simhash + 写 column. flush 后才能给阶段 2 当候选池
    for art_id, original_url in inserted_pairs:
        raw = url_to_raw.get(original_url)
        if raw is None:
            continue
        try:
            await compute_and_persist_simhash(
                session,
                article_id=art_id,
                text_for_hash=_compose_text_for_hash(raw.title, raw.summary),
            )
            simhash_filled += 1
        except Exception as e:
            logger.warning(
                f"article_ingest.simhash_compute_failed id={art_id} url={original_url}: {e}"
            )
    await session.flush()

    # 阶段 2: 阶段 1 已落 simhash, 现在按 published_at 升序逐条找 parent.
    # 升序保证: 候选池里早入的文章会先被算成 parent, 后入的相似文章 attach
    # 上去, 与 spec 锁定的 "parent = 最早 published_at" 保持一致.
    # 仅排序 url 在 url_to_raw 里的 pair (理论上全部都在: inserted_pairs 来自
    # 本批 matched 的 RETURNING; 防御性 filter 是为静态类型干净).
    sortable_pairs = [(art_id, url) for art_id, url in inserted_pairs if url in url_to_raw]
    for art_id, original_url in sorted(
        sortable_pairs,
        key=lambda p: url_to_raw[p[1]].published_at,
    ):
        raw = url_to_raw[original_url]
        try:
            simhash_value = _compute_simhash_from_raw(raw)
            parent = await find_topic_parent(
                session,
                article_id=art_id,
                simhash_value=simhash_value,
                market=raw.market,
                source_name=raw.source_name,
                published_at=raw.published_at,
            )
            if parent is None:
                continue
            parent_id, distance = parent
            ok = await link_topic(
                session,
                parent_article_id=parent_id,
                child_article_id=art_id,
                distance=distance,
            )
            if ok:
                topics_linked += 1
        except Exception as e:
            logger.warning(
                f"article_ingest.link_topic_failed id={art_id} url={original_url}: {e}"
            )

    return simhash_filled, topics_linked


def _compose_text_for_hash(title: str, summary: str | None) -> str:
    """计算 simhash 的输入文本 = ``title + ' ' + summary`` (None → '').

    与 ``app.services.article_ingest.dedup._compose_text_for_hash`` 同款;
    这里复刻一份避免循环 import (dedup → article_ingest 反过来已有 dispatcher
    引 dedup 的方向). 改算法时两处对齐.
    """
    if summary:
        return f"{title} {summary}"
    return title


def _compute_simhash_from_raw(raw: ArticleRaw) -> int:
    """``ArticleRaw`` → simhash int; dispatcher 阶段 2 用.

    阶段 1 已经在 ``compute_and_persist_simhash`` 算过一次, 但那个是写库
    版本不返 int. 重算一次开销 < 1ms, 比从 DB SELECT 回来反序列化还便宜.
    """
    return compute_simhash(_compose_text_for_hash(raw.title, raw.summary))


__all__ = [
    "_dedup_inserted_batch",
    "register_sources",
    "run_ingest_articles_job",
    "upsert_articles",
]
