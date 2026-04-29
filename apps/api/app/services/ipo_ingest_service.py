"""IPO 入库 / Upsert 服务 (BE-007 + BE-S2-000).

职责:
1. 把 ``IPOItem`` (来自 AKShare / hkexnews / seed) upsert 进 ``ipos`` 表.
   - 唯一键 ``(code, market)`` (见 INFRA-001 / models/ipo.py)
   - INSERT 走 PG ``ON CONFLICT DO UPDATE`` 一条 SQL, 避免 N+1
   - 仅更新"会变"的业务字段 (issue_price / listing_date / status / ...);
     不动 PK, ``created_at``.
2. ``run_ingest_a_job()`` (BE-007): A 股 IPO 抓取入口, APScheduler 用.
3. ``run_ingest_hk_job()`` (BE-S2-000): HK IPO 抓取入口, 走 hkexnews 申请人列表.
   - 与 A 股共享 upsert + cache invalidate 逻辑
   - hkexnews 的 ``prospectus_url`` 通过 ``HKApplicantFetchResult.prospectus_urls``
     侧通道传进来, 写到 ``ipos.extra.prospectus_url`` 给 BE-S2-004 招股书入库流水线用
   - 申请阶段尚无真实股票代码, code 用 ``AP-{yyyymmdd}-{slug}.HK`` 占位
   - 失败一律 logger.exception 不抛 (与 A 股一致)

设计要点:
- ``IPOItem`` 只有单字段 ``industry``, ORM 是 ``industry_l1`` / ``industry_l2``
  分级. 当前阶段把 ``industry`` 整体塞进 ``industry_l1``, l2 留 NULL,
  待 Sprint 2 引入分类树后再二次清洗.
- ``IPOItem`` 不带 ``raised_amount`` / ``sponsors`` / ``underwriters``,
  这些字段在 upsert 时不会被覆盖 (用 ``COALESCE(EXCLUDED, ipos.x)`` 保护),
  这样人工补录的字段不会被周期任务擦掉.
- ``prospectus_url`` 走 ``ipos.extra`` JSONB 侧字段而非顶层列, 兼容 BE-009
  ``IPODetail.prospectus_url`` 已从 extra 读的存储格式 (spec/09 §BE-S2-000 §决策 #2).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import (
    aastocks_ipo_client,
    akshare_client,
    eastmoney_ipo_client,
    hkex_client,
)
from app.cache import invalidate_namespace
from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import IPO
from app.schemas.ipo import IPOItem


def _ipo_item_to_row(
    item: IPOItem,
    *,
    extra_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``IPOItem`` (Pydantic) → ``ipos`` 表 INSERT row dict.

    保持显式 mapping, 不用 ``model_dump``: schema 字段名 ↔ 列名不是 1:1.

    ``extra_overrides`` 让 HK ingest (BE-S2-000) 把 ``prospectus_url`` 走侧通道
    塞进 ``extra`` (``IPOItem`` schema 没这字段, 但 BE-009 ``IPODetail`` 已从
    ``extra.prospectus_url`` 读, 走同一存储格式).
    """
    extra: dict[str, Any] = {
        "one_lot_winning_rate": (
            float(item.one_lot_winning_rate)
            if item.one_lot_winning_rate is not None
            else None
        ),
        "schema_updated_at": (
            item.updated_at.isoformat() if item.updated_at else None
        ),
    }
    if extra_overrides:
        extra.update(extra_overrides)
    return {
        "code": item.code,
        "name": item.name,
        "market": item.market,
        "industry_l1": item.industry,
        "industry_l2": None,
        "issue_price": item.issue_price,
        # BUG-S6.8-004: 招股价区间双列 + 老 issue_price 兼容 (== price_max)
        "price_min": item.price_min,
        "price_max": item.price_max,
        "issue_currency": item.issue_currency,
        "listing_date": item.listing_date,
        "subscribe_start": item.subscribe_start,
        "subscribe_end": item.subscribe_end,
        "pe_ratio": item.pe_ratio,
        "raised_amount": item.raised_amount,
        "status": item.status if item.status != "unknown" else None,
        "data_source": item.data_source or None,
        "extra": extra,
    }


async def upsert_ipos(
    session: AsyncSession,
    items: list[IPOItem],
    *,
    extra_per_code: dict[str, dict[str, Any]] | None = None,
) -> dict[str, int]:
    """批量 upsert ``IPOItem`` 进 ``ipos``.

    返回 ``{"received": n, "inserted": x, "updated": y, "skipped": z}``.

    实现:
    - 用 PG ``INSERT ... ON CONFLICT (code, market) DO UPDATE`` 一条 SQL 跑批,
      避免 ORM 一条一条 select-update 的 N+1.
    - "新值非 NULL 才覆盖"字段都用 ``COALESCE(EXCLUDED.x, ipos.x)`` 兜底,
      防止周期任务把人工录入的字段擦了; ``name`` / ``updated_at`` 这种则强制覆盖.
    - 提交事务由调用方控制 (run_ingest_a_job / 测试 / 路由).
    - inserted / updated 计数靠先查一遍现存 ``(code, market)`` 集合, 计数仅供
      log / metric 参考, 不参与正确性, 因此采用 row constructor IN 子句精确查
      本批次涉及的 keys, 不会拉无关数据.
    """
    if not items:
        return {"received": 0, "inserted": 0, "updated": 0, "skipped": 0}

    rows = [
        _ipo_item_to_row(
            it,
            extra_overrides=(extra_per_code or {}).get(it.code),
        )
        for it in items
    ]
    keys = [(r["code"], r["market"]) for r in rows]

    existing_q = select(IPO.code, IPO.market).where(
        tuple_(IPO.code, IPO.market).in_(keys)
    )
    existing_rows = (await session.execute(existing_q)).all()
    existing_set = {(r.code, r.market) for r in existing_rows}

    expected_inserts = sum(1 for k in keys if k not in existing_set)
    expected_updates = len(keys) - expected_inserts

    stmt = pg_insert(IPO.__table__).values(rows)  # type: ignore[arg-type]
    excl = stmt.excluded
    cur = IPO.__table__.c

    # ``extra`` 用 JSONB merge 操作符 ``||`` (PG): ingest 写的 key 覆盖, 其它 key
    # (BE-S2-004 招股书 RAG 写的 highlights/risks/financial_summary) 不被擦掉.
    # ``COALESCE`` 兜底 NULL → '{}' 防 NULL || jsonb 退化成 NULL.
    extra_merged = func.coalesce(cur.extra, sa_text("'{}'::jsonb")).op("||")(excl.extra)

    update_payload: dict[str, Any] = {
        "industry_l1": func.coalesce(excl.industry_l1, cur.industry_l1),
        "issue_price": func.coalesce(excl.issue_price, cur.issue_price),
        "price_min": func.coalesce(excl.price_min, cur.price_min),
        "price_max": func.coalesce(excl.price_max, cur.price_max),
        "issue_currency": func.coalesce(excl.issue_currency, cur.issue_currency),
        "listing_date": func.coalesce(excl.listing_date, cur.listing_date),
        "subscribe_start": func.coalesce(excl.subscribe_start, cur.subscribe_start),
        "subscribe_end": func.coalesce(excl.subscribe_end, cur.subscribe_end),
        "pe_ratio": func.coalesce(excl.pe_ratio, cur.pe_ratio),
        "raised_amount": func.coalesce(excl.raised_amount, cur.raised_amount),
        "status": func.coalesce(excl.status, cur.status),
        "data_source": func.coalesce(excl.data_source, cur.data_source),
        "name": excl.name,
        "extra": extra_merged,
        "updated_at": func.now(),
    }

    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=["code", "market"],
        set_=update_payload,
    )
    await session.execute(upsert_stmt)
    await session.flush()

    return {
        "received": len(rows),
        "inserted": expected_inserts,
        "updated": expected_updates,
        "skipped": 0,
    }


async def run_ingest_a_job(settings: Settings | None = None) -> dict[str, int]:
    """APScheduler 回调入口: 抓 A 股 IPO 列表 → upsert 进库.

    设计:
    - 不抛异常: 任何失败 (网络 / DB / 解析) 都 logger.exception 后返回 stats,
      防止 scheduler 把整个 job 标 failed 后停掉.
    - 自己开 session: 调度器不在请求作用域内, 不能复用 ``Depends(get_session)``.
    - HK 暂用 seed, 不抓 (只会撞 seed 的 3 行老数据, 没意义), Sprint 2 切真源后启用.
    """
    settings = settings or get_settings()
    stats = {"received": 0, "inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    try:
        items = await akshare_client.fetch_a_ipos(
            limit=settings.ipo_ingest_a_limit
        )
    except Exception as e:
        logger.exception(f"ipo_ingest.fetch_a_failed: {e}")
        stats["errors"] = 1
        return stats

    if not items:
        logger.warning("ipo_ingest.fetch_a empty (akshare returned 0 rows)")
        return stats

    factory = get_session_factory()
    try:
        async with factory() as session:
            stats_db = await upsert_ipos(session, items)
            await session.commit()
        for k, v in stats_db.items():
            stats[k] = v
    except Exception as e:
        logger.exception(f"ipo_ingest.upsert_failed items={len(items)}: {e}")
        stats["errors"] = 1
        return stats

    # ingest 落库后清 BE-008 / BE-009 写入的缓存, 让下一次 ``GET /ipos`` /
    # ``GET /ipos/{code}`` 立刻回源新数据 (否则最差 10/30 min stale).
    # 失效失败本身已被 invalidate_namespace 内部 catch + warn, 不影响 ingest 成功状态.
    stats["cache_invalidated"] = await invalidate_namespace(
        "ipos:list", "ipos:detail"
    )

    logger.info(
        f"ipo_ingest.a.ok received={stats['received']} "
        f"inserted~={stats['inserted']} updated~={stats['updated']} "
        f"cache_invalidated={stats['cache_invalidated']}"
    )
    return stats


async def run_ingest_hk_job(settings: Settings | None = None) -> dict[str, int]:
    """APScheduler 回调入口: 抓 HK IPO 全量 → upsert 进库.

    BUG-S6.7-004 / spec/16: HK 现采用 **三源合并** (eastmoney + aastocks + hkexnews):

    ============== ====================== ====================== =====================
    源              覆盖范围               主要字段               data_source 标记
    ============== ====================== ====================== =====================
    eastmoney       50 行 listed (近 3 月)  招股价 / 招股股数 /
                                            募集资金 / 上市日期    eastmoney-ipolist
    **aastocks**    招股期 (subscribing /   招股价 / 招股截止日 /
                    upcoming) 5-15 行       行业 / 上市日期        aastocks-upcoming
    hkexnews        PreIPO 申请人 (占位)    招股书 PDF + 提交日期  hkexnews-applicants
    ============== ====================== ====================== =====================

    Sprint 6.6 (spec/15) 选定东方财富 ipolist 解决了"假数据"问题, 但用户验收
    发现可孚医疗 / 天星医疗这类**正在招股**的真新股拿不到 — 因为 ipolist 只
    收已确定上市日期的 IPO. Sprint 6.7 spike 后引入 AAStocks ``upcomingipo.aspx``
    专门覆盖 subscribing/upcoming 这个东方财富的结构性盲区.

    设计:
    - 不抛异常: 任一源失败 logger.exception 后返回 stats,
      防 scheduler 把整个 job 标 failed 后停掉. **三源相互独立**.
    - 自己开 session: 调度器不在请求作用域内, 不能复用 ``Depends(get_session)``.
    - 三源 upsert 顺序: eastmoney 先 (字段全, listed) → aastocks 后 (subscribing
      覆盖) → hkexnews 最后 (PreIPO 占位 code 与真代码不冲突). PG ON CONFLICT
      DO UPDATE 用 ``COALESCE(EXCLUDED, current)``, 不会覆盖前源已写的非 NULL
      字段, 所以顺序对结果幂等.
    - aastocks 重叠 listed 行 (天星 listed 后还会出现在 ipolist 列表里) —
      eastmoney 先写 status='listed', aastocks 后写 status='subscribing' 时
      ``COALESCE(EXCLUDED.status, current.status)`` 保留 'listed' (后写不
      覆盖). 等下一周期 aastocks upcoming 不再含天星, 此行就稳定 listed.
    - hkexnews 的 ``prospectus_url`` 走 ``extra_per_code`` 侧通道塞进
      ``ipos.extra.prospectus_url`` (BE-009 ``IPODetail`` 已从这里读, 不变).

    返回值:
        ``{"received": N, "inserted": x, "updated": y, "skipped": 0,
            "errors": e, "cache_invalidated": k, "with_pdf": p,
            "em_received": E, "em_errors": EE,
            "aa_received": A, "aa_errors": AE}``
        ``em_*`` / ``aa_*`` 为各源子统计 (出问题时方便定位).
    """
    settings = settings or get_settings()
    stats: dict[str, int] = {
        "received": 0,
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "cache_invalidated": 0,
        "with_pdf": 0,
        "em_received": 0,
        "em_errors": 0,
        "aa_received": 0,
        "aa_errors": 0,
    }

    factory = get_session_factory()

    # ── 主源: 东方财富 (BUG-S6.6-004) ─────────────────────────
    try:
        em_result = await eastmoney_ipo_client.fetch_eastmoney_ipo_list(
            settings=settings,
            limit=settings.ipo_ingest_hk_limit,
        )
    except Exception as e:
        logger.exception(f"ipo_ingest.fetch_eastmoney_failed: {e}")
        em_result = eastmoney_ipo_client.EastmoneyIPOFetchResult.empty()
        stats["em_errors"] = 1

    if em_result.items:
        # BUG-S6.7-002: total_shares 通过 extra_per_code 侧通道 (与 prospectus_url
        # 同款协议) 塞进 ``ipos.extra.total_shares``, 详情页 IPODetail 从这里读.
        # 序列化为 str 让 PG JSONB 友好 (Decimal 不直接 JSONB 化).
        em_extra: dict[str, dict[str, Any]] = {
            code: {"total_shares": str(shares)}
            for code, shares in em_result.total_shares_by_code.items()
        }
        try:
            async with factory() as session:
                em_stats = await upsert_ipos(
                    session, em_result.items, extra_per_code=em_extra
                )
                await session.commit()
            stats["em_received"] = em_stats["received"]
            for k in ("received", "inserted", "updated", "skipped"):
                stats[k] += em_stats[k]
        except Exception as e:
            logger.exception(
                f"ipo_ingest.eastmoney_upsert_failed items={len(em_result.items)}: {e}"
            )
            stats["em_errors"] = 1
    else:
        logger.warning("ipo_ingest.fetch_eastmoney empty (got 0 rows from ipolist.html)")

    # ── 补全源: AAStocks 招股期 IPO (BUG-S6.7-003) ────────────
    # 专门覆盖东方财富 ipolist 缺的 subscribing/upcoming 段 (天星/可孚等正在招股的港股).
    # 失败不抛 — eastmoney 已经覆盖 listed 主路径, aastocks 失败也只是回退到"看不
    # 到招股期", 不影响已上市新股展示.
    try:
        aa_result = await aastocks_ipo_client.fetch_aastocks_upcoming(
            settings=settings,
            limit=settings.ipo_ingest_hk_limit,
        )
    except Exception as e:
        logger.exception(f"ipo_ingest.fetch_aastocks_failed: {e}")
        aa_result = aastocks_ipo_client.AAStocksIPOFetchResult.empty()
        stats["aa_errors"] = 1

    if aa_result.items:
        try:
            async with factory() as session:
                aa_stats = await upsert_ipos(session, aa_result.items)
                await session.commit()
            stats["aa_received"] = aa_stats["received"]
            for k in ("received", "inserted", "updated", "skipped"):
                stats[k] += aa_stats[k]
        except Exception as e:
            logger.exception(
                f"ipo_ingest.aastocks_upsert_failed items={len(aa_result.items)}: {e}"
            )
            stats["aa_errors"] = 1
    else:
        logger.info(
            "ipo_ingest.fetch_aastocks empty "
            "(no upcoming/subscribing IPOs in window — 正常情况)"
        )

    # ── 补充源: hkexnews 申请人列表 (PreIPO 占位 + 招股书 PDF) ─
    # BE-S2-000 历史路径, 字段少但能补 "已交 A1 申请但还没公开招股价" 的早期 IPO.
    # 失败不抛 — eastmoney 已经覆盖主路径, hkexnews 只是锦上添花.
    try:
        hk_result = await hkex_client.fetch_hk_applicants(
            settings=settings,
            limit=settings.ipo_ingest_hk_limit,
        )
    except Exception as e:
        logger.exception(f"ipo_ingest.fetch_hkexnews_failed: {e}")
        hk_result = hkex_client.HKApplicantFetchResult.empty()

    if hk_result.items:
        extra_per_code: dict[str, dict[str, Any]] = {
            code: {"prospectus_url": url}
            for code, url in hk_result.prospectus_urls.items()
        }
        try:
            async with factory() as session:
                hk_stats = await upsert_ipos(
                    session,
                    hk_result.items,
                    extra_per_code=extra_per_code,
                )
                await session.commit()
            stats["with_pdf"] = len(hk_result.prospectus_urls)
            for k in ("received", "inserted", "updated", "skipped"):
                stats[k] += hk_stats[k]
        except Exception as e:
            logger.exception(
                f"ipo_ingest.hkexnews_upsert_failed items={len(hk_result.items)}: {e}"
            )
            stats["errors"] = 1

    if stats["received"] == 0:
        logger.warning("ipo_ingest.hk both sources returned empty")
        return stats

    stats["cache_invalidated"] = await invalidate_namespace(
        "ipos:list", "ipos:detail"
    )

    logger.info(
        f"ipo_ingest.hk.ok received={stats['received']} "
        f"em={stats['em_received']} aa={stats['aa_received']} "
        f"hkexnews_with_pdf={stats['with_pdf']} "
        f"inserted~={stats['inserted']} updated~={stats['updated']} "
        f"cache_invalidated={stats['cache_invalidated']}"
    )
    return stats


__all__ = [
    "upsert_ipos",
    "run_ingest_a_job",
    "run_ingest_hk_job",
]
