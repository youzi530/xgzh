"""IPO 入库 / Upsert 服务 (BE-007).

职责:
1. 把 ``IPOItem`` (来自 AKShare / seed) upsert 进 ``ipos`` 表.
   - 唯一键 ``(code, market)`` (见 INFRA-001 / models/ipo.py)
   - INSERT 走 PG ``ON CONFLICT DO UPDATE`` 一条 SQL, 避免 N+1
   - 仅更新"会变"的业务字段 (issue_price / listing_date / status / ...);
     不动 PK, ``created_at``.
2. ``run_ingest_a_job()`` 是 APScheduler 的入口.
   - 自己开/关 session, 失败 logger.exception 但**不**抛, 防止 scheduler 把
     整个 job 标 failed 然后停掉.
3. HK 仍用 seed (akshare 1.18 没干净的 HK IPO API), 这里只跑 A.
   Sprint 2 接 HKEX 后, 把 ``run_ingest_hk_job`` 一起挂上去即可.

注意:
- ``IPOItem`` 只有单字段 ``industry``, ORM 是 ``industry_l1`` / ``industry_l2``
  分级. 当前阶段把 ``industry`` 整体塞进 ``industry_l1``, l2 留 NULL,
  待 Sprint 2 引入分类树后再二次清洗.
- ``IPOItem`` 不带 ``raised_amount`` / ``sponsors`` / ``underwriters``,
  这些字段在 upsert 时不会被覆盖 (用 ``COALESCE(EXCLUDED, ipos.x)`` 保护),
  这样人工补录的字段不会被周期任务擦掉.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import akshare_client
from app.cache import invalidate_namespace
from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import IPO
from app.schemas.ipo import IPOItem


def _ipo_item_to_row(item: IPOItem) -> dict[str, Any]:
    """``IPOItem`` (Pydantic) → ``ipos`` 表 INSERT row dict.

    保持显式 mapping, 不用 ``model_dump``: schema 字段名 ↔ 列名不是 1:1.
    """
    return {
        "code": item.code,
        "name": item.name,
        "market": item.market,
        "industry_l1": item.industry,
        "industry_l2": None,
        "issue_price": item.issue_price,
        "issue_currency": item.issue_currency,
        "listing_date": item.listing_date,
        "subscribe_start": item.subscribe_start,
        "subscribe_end": item.subscribe_end,
        "pe_ratio": item.pe_ratio,
        "raised_amount": item.raised_amount,
        "status": item.status if item.status != "unknown" else None,
        "data_source": item.data_source or None,
        "extra": {
            "one_lot_winning_rate": (
                float(item.one_lot_winning_rate)
                if item.one_lot_winning_rate is not None
                else None
            ),
            "schema_updated_at": (
                item.updated_at.isoformat() if item.updated_at else None
            ),
        },
    }


async def upsert_ipos(
    session: AsyncSession, items: list[IPOItem]
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

    rows = [_ipo_item_to_row(it) for it in items]
    keys = [(r["code"], r["market"]) for r in rows]

    existing_q = select(IPO.code, IPO.market).where(
        tuple_(IPO.code, IPO.market).in_(keys)
    )
    existing_rows = (await session.execute(existing_q)).all()
    existing_set = {(r.code, r.market) for r in existing_rows}

    expected_inserts = sum(1 for k in keys if k not in existing_set)
    expected_updates = len(keys) - expected_inserts

    stmt = pg_insert(IPO.__table__).values(rows)
    excl = stmt.excluded
    cur = IPO.__table__.c

    update_payload: dict[str, Any] = {
        "industry_l1": func.coalesce(excl.industry_l1, cur.industry_l1),
        "issue_price": func.coalesce(excl.issue_price, cur.issue_price),
        "issue_currency": func.coalesce(excl.issue_currency, cur.issue_currency),
        "listing_date": func.coalesce(excl.listing_date, cur.listing_date),
        "subscribe_start": func.coalesce(excl.subscribe_start, cur.subscribe_start),
        "subscribe_end": func.coalesce(excl.subscribe_end, cur.subscribe_end),
        "pe_ratio": func.coalesce(excl.pe_ratio, cur.pe_ratio),
        "raised_amount": func.coalesce(excl.raised_amount, cur.raised_amount),
        "status": func.coalesce(excl.status, cur.status),
        "data_source": func.coalesce(excl.data_source, cur.data_source),
        "name": excl.name,
        "extra": excl.extra,
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


__all__ = [
    "upsert_ipos",
    "run_ingest_a_job",
]
