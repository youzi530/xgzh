"""IPO 业务服务 (BE-008: ``list_ipos`` 切回 DB + 筛选 + 分页 + Redis 缓存).

读路径分两条:
- ``A`` / ``US``: 从 ``ipos`` 表查 (BE-007 周期任务把 AKShare 数据 upsert 进来),
  支持按 ``status`` / ``industry`` 过滤、分页、排序.
- ``HK``: 走 ``akshare_client.fetch_hk_ipos`` 内置 seed (akshare 1.18 没干净的 HK
  IPO API), 内存做筛选+分页. Sprint 2 接 HKEX 后切回 DB 路径.

缓存:
- ``@cached(ttl_seconds=600, namespace="ipos:list")`` 套在 ``list_ipos`` 入口上.
  Cache key 含全部筛选/分页参数 hash (装饰器内部 ``_hash_args`` 自动算).
  Stale 上限 10min, 与 BE-007 cron 12h 抓一次相比已经够新鲜.
- 缓存读写失败均 fail-open (装饰器自带), 不影响业务可用性.
- 故意把 ``IPOListResponse`` 而不是 ``list[IPOItem]`` 进缓存: 这样
  ``total`` / ``page`` / ``size`` 一起被缓存, 反序列化回来就是完整响应.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters import akshare_client
from app.cache import cached
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import IPO
from app.schemas.ipo import IPOItem, IPOListResponse, IPOStatus, Market

LIST_CACHE_TTL_SECONDS = 600


def _orm_to_item(row: IPO) -> IPOItem:
    """ORM ``IPO`` → schema ``IPOItem``.

    ORM 拿数据用 SQLAlchemy 类型 (Decimal/datetime), Pydantic 自己处理.
    ``one_lot_winning_rate`` 我们藏在 ``extra`` JSONB 里 (ipo_ingest_service
    入库时塞进去的), 这里读出来回灌到 schema.
    """
    extra = row.extra or {}
    one_lot = extra.get("one_lot_winning_rate") if isinstance(extra, dict) else None

    return IPOItem(
        code=row.code,
        name=row.name,
        market=cast(Market, row.market),
        industry=row.industry_l1,
        issue_price=row.issue_price,
        issue_currency=row.issue_currency,
        listing_date=row.listing_date,
        subscribe_start=row.subscribe_start,
        subscribe_end=row.subscribe_end,
        pe_ratio=row.pe_ratio,
        raised_amount=row.raised_amount,
        one_lot_winning_rate=one_lot,
        status=cast(IPOStatus, row.status or "unknown"),
        data_source=row.data_source or "",
        updated_at=row.updated_at,
    )


async def _list_ipos_db(
    factory: async_sessionmaker[AsyncSession],
    *,
    market: Market,
    status: IPOStatus | None,
    industry: str | None,
    page: int,
    size: int,
) -> tuple[list[IPOItem], int]:
    """打 DB 查 IPO 列表 + 总数.

    排序: ``listing_date DESC NULLS LAST, code ASC`` — 已上市的按时间倒排,
    没 listing_date 的 (upcoming/withdrawn) 排到最末; 同一天上市按 code 稳定排序,
    避免 page 跳页时顺序漂移.
    """
    base = select(IPO).where(IPO.market == market)
    count_base = select(func.count()).select_from(IPO).where(IPO.market == market)

    if status is not None:
        base = base.where(IPO.status == status)
        count_base = count_base.where(IPO.status == status)
    if industry is not None:
        base = base.where(IPO.industry_l1 == industry)
        count_base = count_base.where(IPO.industry_l1 == industry)

    base = (
        base.order_by(
            IPO.listing_date.desc().nulls_last(),
            IPO.code.asc(),
        )
        .limit(size)
        .offset((page - 1) * size)
    )

    async with factory() as session:
        rows = (await session.execute(base)).scalars().all()
        total = (await session.execute(count_base)).scalar_one()

    items = [_orm_to_item(r) for r in rows]
    return items, int(total)


def _filter_seed(
    items: list[IPOItem],
    *,
    status: IPOStatus | None,
    industry: str | None,
) -> list[IPOItem]:
    """HK seed 暂时在内存里做筛选 (akshare 没干净的 HK IPO API)."""
    out = items
    if status is not None:
        out = [it for it in out if it.status == status]
    if industry is not None:
        out = [it for it in out if it.industry == industry]
    return out


@cached(ttl_seconds=LIST_CACHE_TTL_SECONDS, namespace="ipos:list")
async def list_ipos(
    *,
    market: Market = "HK",
    status: IPOStatus | None = None,
    industry: str | None = None,
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """列出指定 market 下的 IPO. keyword-only 让缓存 key hash 稳定.

    - ``market="A"``: 从 ``ipos`` 表查
    - ``market="HK"``: 走 seed (Sprint 2 接 HKEX 后切 DB)
    - ``market="US"``: 暂无数据源, 返回空列表 (Sprint 3+)

    返回 ``dict`` 而非 ``IPOListResponse``: ``@cached`` 用 ``json.dumps`` 写缓存,
    Pydantic 实例不能直接 dump, 命中后 ``json.loads`` 拿到的也是 dict. 让 service
    层始终在 dict 边界上, 路由层再 ``IPOListResponse.model_validate`` 重构成 schema.
    """
    if market == "HK":
        seed = await akshare_client.fetch_hk_ipos(limit=200)
        filtered = _filter_seed(seed, status=status, industry=industry)
        total = len(filtered)
        start = (page - 1) * size
        items = filtered[start : start + size]
    elif market == "US":
        logger.info("list_ipos.us not_implemented yet, return empty")
        items, total = [], 0
    else:
        factory = get_session_factory()
        items, total = await _list_ipos_db(
            factory,
            market=market,
            status=status,
            industry=industry,
            page=page,
            size=size,
        )

    payload = IPOListResponse(
        items=items, total=total, market=market, page=page, size=size
    )
    return payload.model_dump(mode="json")


async def get_ipo(code: str) -> IPOItem | None:
    """通过代码精确查询新股.

    第一刀简单实现: A/US 走 DB, HK 走 seed 列表扫描. BE-009 会做多源 merge
    (HKEX 字段 + AKShare 财务数据 + 招股书要点) 和详情字段补全.
    """
    code_upper = code.upper().strip()
    market: Market = "HK" if code_upper.endswith(".HK") else "A"

    if market == "HK":
        seed = await akshare_client.fetch_hk_ipos(limit=500)
        for it in seed:
            if it.code.upper() == code_upper:
                return it
        return None

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(IPO).where(IPO.code == code_upper, IPO.market == market)
            )
        ).scalar_one_or_none()
    return _orm_to_item(row) if row else None


__all__ = ["list_ipos", "get_ipo", "LIST_CACHE_TTL_SECONDS"]
