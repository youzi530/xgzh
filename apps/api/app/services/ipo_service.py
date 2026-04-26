"""IPO 业务服务.

BE-008: ``list_ipos`` 切回 DB + 筛选 + 分页 + Redis 缓存
BE-009: ``get_ipo_detail`` 多源字段聚合 + 30min 缓存 (sponsors / highlights / 招股书 url)
BE-S2-000: HK 路径切 DB (与 A 股一致), 仅在 DB 空表时 fallback 到 hkex_client cold-start seed

读路径分两条:
- ``A`` / ``HK`` / ``US``: 统一走 ``ipos`` 表 (BE-007 周期任务抓 A 股, BE-S2-000
  抓 HK 申请人列表后 upsert 进来), 支持 ``status`` / ``industry`` 筛选 + 分页 + 排序
- **cold-start fallback**: ``market="HK"`` 且 DB 空表 (lifespan 第一次启动尚未跑完
  hk ingest) 时, fallback 到 ``hkex_client.get_cold_start_seed`` 让首次部署的用户
  不看到空首页. 缓存命中后 (10min TTL) 后台 ingest 跑完会自动 invalidate.

缓存:
- 列表: ``@cached(ttl=600s, namespace="ipos:list")`` (BE-008)
- 详情: ``@cached(ttl=1800s, namespace="ipos:detail")`` (BE-009) — 详情写得更慢,
  缓存 30min 比列表 10min 长一倍, 因为 highlights/sponsors 等字段 12h 内基本不变.
- 缓存读写失败均 fail-open (装饰器自带), 不影响业务可用性.
- 故意把 ``IPOListResponse`` / ``IPODetail`` 序列化后的 dict 而不是 Pydantic 实例
  进缓存: ``json.dumps(BaseModel)`` 不是直接 JSON-friendly, 让 service 边界始终
  在 dict 上, 路由层再 ``model_validate`` 重构成 schema.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters import hkex_client
from app.cache import cached
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import IPO
from app.schemas.ipo import IPODetail, IPOItem, IPOListResponse, IPOStatus, Market

LIST_CACHE_TTL_SECONDS = 600
DETAIL_CACHE_TTL_SECONDS = 1800


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
    """cold-start seed 内存筛选 (HK DB 空表时 fallback 用)."""
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

    - ``market="A"`` / ``market="HK"``: 从 ``ipos`` 表查 (BE-007 / BE-S2-000 抓)
    - ``market="HK"`` 且 DB 空表: fallback 到 ``hkex_client.get_cold_start_seed``,
      让首次部署的用户不看到空首页 (lifespan 启动时 hk ingest 会异步抓数据,
      缓存 600s TTL 内被 ingest invalidate 后下次直接走 DB)
    - ``market="US"``: 暂无数据源, 返回空列表 (Sprint 3+)

    返回 ``dict`` 而非 ``IPOListResponse``: ``@cached`` 用 ``json.dumps`` 写缓存,
    Pydantic 实例不能直接 dump, 命中后 ``json.loads`` 拿到的也是 dict. 让 service
    层始终在 dict 边界上, 路由层再 ``IPOListResponse.model_validate`` 重构成 schema.
    """
    if market == "US":
        logger.info("list_ipos.us not_implemented yet, return empty")
        items: list[IPOItem] = []
        total = 0
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
        # HK 冷启动 fallback: DB 空表且第一页第一次请求 → 走 seed (3 条)
        # 不动 status="HK" 但有 status/industry 筛选的请求 (用户主动筛掉 seed
        # 不在的 industry 时仍返回空, 这是正确的 — 不污染筛选结果)
        if (
            market == "HK"
            and total == 0
            and status is None
            and industry is None
            and page == 1
        ):
            seed = hkex_client.get_cold_start_seed(limit=size)
            if seed:
                logger.info(
                    f"list_ipos.hk_cold_start_fallback returning {len(seed)} seed items"
                )
                items = seed
                total = len(seed)

    payload = IPOListResponse(
        items=items, total=total, market=market, page=page, size=size
    )
    return payload.model_dump(mode="json")


async def get_ipo(code: str) -> IPOItem | None:
    """轻量"基础信息"查询: 仅 ``IPOItem`` 字段, 给 Agent SSE prompt 用.

    路由层用 :func:`get_ipo_detail` 拿完整 ``IPODetail``; 这里保留作为
    内部 helper 是因为 ``agent_service.diagnose_stream`` 只需要 ``IPOItem`` 级别
    的 prompt context, 拿 detail 反而冗余.

    BE-S2-000: HK / A 统一走 DB; HK 在 DB 没命中时再扫 cold-start seed (兜底).
    """
    code_upper = code.upper().strip()
    market: Market = "HK" if code_upper.endswith(".HK") else "A"

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(IPO).where(IPO.code == code_upper, IPO.market == market)
            )
        ).scalar_one_or_none()
    if row:
        return _orm_to_item(row)

    if market == "HK":
        for it in hkex_client.get_cold_start_seed(limit=50):
            if it.code.upper() == code_upper:
                return it
    return None


def _orm_to_detail(row: IPO) -> IPODetail:
    """ORM ``IPO`` → schema ``IPODetail``: 在 ``_orm_to_item`` 基础上补详情字段.

    - ``sponsors`` / ``underwriters`` / ``prospectus_url``: 直接读 ORM 同名列
    - ``highlights`` / ``risks`` / ``financial_summary``: 从 ``ipos.extra`` JSONB
      中解出 (BE-018 招股书 RAG 写入; 当前若无则为空 list / None)
    """
    base = _orm_to_item(row)
    extra = row.extra or {}
    if not isinstance(extra, dict):
        extra = {}

    highlights = extra.get("highlights")
    if not isinstance(highlights, list):
        highlights = []
    risks = extra.get("risks")
    if not isinstance(risks, list):
        risks = []
    fin = extra.get("financial_summary")
    if not isinstance(fin, dict):
        fin = None

    return IPODetail(
        **base.model_dump(),
        prospectus_url=row.prospectus_url,
        sponsors=row.sponsors,
        underwriters=row.underwriters,
        highlights=[str(h) for h in highlights if h is not None],
        risks=[str(r) for r in risks if r is not None],
        financial_summary=fin,
    )


def _seed_to_detail(item: IPOItem) -> IPODetail:
    """HK seed 升级为 ``IPODetail``: 没有招股书/保荐人结构化数据, 全空."""
    return IPODetail(**item.model_dump())


@cached(ttl_seconds=DETAIL_CACHE_TTL_SECONDS, namespace="ipos:detail")
async def get_ipo_detail(code: str) -> dict[str, Any] | None:
    """新股详情 (BE-009): 全走 DB, HK 在 DB miss 时再扫 cold-start seed; 30min Redis 缓存.

    返回 ``dict`` (``IPODetail`` 序列化后), 让缓存 JSON 可读; 路由层再
    ``IPODetail.model_validate`` 重构. 不存在返回 ``None`` (装饰器
    ``skip_if_none=True``, 不会进缓存, 避免错误穿透).

    BE-S2-000: HK 路径与 A 股统一从 ``ipos`` 表查; DB 没命中时再回 cold-start seed
    (Sprint 1 时 HK 永远走 seed; Sprint 2 后 seed 仅作 DB 空表兜底).
    """
    code_upper = code.upper().strip()
    market: Market = "HK" if code_upper.endswith(".HK") else "A"

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                select(IPO).where(IPO.code == code_upper, IPO.market == market)
            )
        ).scalar_one_or_none()

    if row:
        return _orm_to_detail(row).model_dump(mode="json")

    if market == "HK":
        for it in hkex_client.get_cold_start_seed(limit=50):
            if it.code.upper() == code_upper:
                return _seed_to_detail(it).model_dump(mode="json")
    return None


__all__ = [
    "list_ipos",
    "get_ipo",
    "get_ipo_detail",
    "LIST_CACHE_TTL_SECONDS",
    "DETAIL_CACHE_TTL_SECONDS",
]
