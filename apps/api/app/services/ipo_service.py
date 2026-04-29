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

from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from sqlalchemy import extract, func, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters import hkex_client
from app.cache import cached
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import IPO
from app.schemas.ipo import (
    HistoricalIPOItem,
    HistoricalIPOListResponse,
    HistoricalSortBy,
    IPODetail,
    IPOItem,
    IPOListResponse,
    IPOPeerAggregate,
    IPOPeerScatterPoint,
    IPOPeerStats,
    IPOStatus,
    Market,
)

LIST_CACHE_TTL_SECONDS = 600
DETAIL_CACHE_TTL_SECONDS = 1800
# Sprint 4 BE-S4-003: 历史列表/行业聚合走更长缓存 — 历史数据 10 min 滞后无影响
HISTORICAL_LIST_CACHE_TTL_SECONDS = 600
PEER_AGGREGATE_CACHE_TTL_SECONDS = 600
PEER_AGGREGATE_MIN_SAMPLES = 5
PEER_SCATTER_MAX_POINTS = 50


def _orm_to_item(row: IPO) -> IPOItem:
    """ORM ``IPO`` → schema ``IPOItem``.

    ORM 拿数据用 SQLAlchemy 类型 (Decimal/datetime), Pydantic 自己处理.
    ``one_lot_winning_rate`` 优先读新加的顶级列 (Sprint 4 BE-S4-001 引入), 兜底
    回 ``extra.one_lot_winning_rate`` (Sprint 1 / 2 ipo_ingest_service 历史写法).
    """
    one_lot = row.one_lot_winning_rate
    if one_lot is None:
        extra = row.extra if isinstance(row.extra, dict) else {}
        legacy = extra.get("one_lot_winning_rate")
        if legacy is not None:
            from decimal import Decimal as _Dec

            try:
                one_lot = _Dec(str(legacy))
            except (ValueError, TypeError):
                one_lot = None

    return IPOItem(
        code=row.code,
        name=row.name,
        market=cast(Market, row.market),
        industry=row.industry_l1,
        issue_price=row.issue_price,
        price_min=row.price_min,
        price_max=row.price_max,
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

    # BUG-S6.7-002: 招股股数 走 extra JSONB 旁路 (与 highlights 一致, 0 alembic 迁移).
    # ingest (eastmoney_ipo_client) 写时塞 ``extra.total_shares = "4.26268e7"`` (str
    # 序列化, JSONB 友好); 详情读出时尝试解 Decimal, 失败 → None.
    total_shares_raw = extra.get("total_shares")
    total_shares: Decimal | None = None
    if total_shares_raw is not None:
        try:
            total_shares = Decimal(str(total_shares_raw))
        except (InvalidOperation, ValueError, TypeError):
            total_shares = None

    return IPODetail(
        **base.model_dump(),
        prospectus_url=row.prospectus_url,
        sponsors=row.sponsors,
        underwriters=row.underwriters,
        highlights=[str(h) for h in highlights if h is not None],
        risks=[str(r) for r in risks if r is not None],
        financial_summary=fin,
        total_shares=total_shares,
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


# ─── Sprint 4 BE-S4-003: 历史 IPO 列表 + 行业聚合 ──────────────────


def _orm_to_historical_item(row: IPO) -> HistoricalIPOItem:
    """ORM IPO → HistoricalIPOItem (含上市后 3 字段 + sponsors + industry_l2)."""
    base = _orm_to_item(row)
    return HistoricalIPOItem(
        **base.model_dump(),
        industry_l2=row.industry_l2,
        first_day_change_pct=row.first_day_change_pct,
        oversubscribe_multiple=row.oversubscribe_multiple,
        sponsors=row.sponsors,
    )


def _build_historical_query(
    *,
    market: Market | None,
    industry: str | None,
    year_from: int | None,
    year_to: int | None,
    sponsor: str | None,
) -> tuple[Any, Any]:
    """构造 ``ipos`` 历史筛选基础查询 (status='listed' + 多维 WHERE).

    返回 (base_select, count_select); 路径走 partial 索引 ``ix_ipos_status_listing_date``
    + ``ix_ipos_industry_year`` + JSONB ``sponsors`` GIN.
    """
    base = select(IPO).where(IPO.status == "listed")
    count_base = (
        select(func.count()).select_from(IPO).where(IPO.status == "listed")
    )

    if market is not None:
        base = base.where(IPO.market == market)
        count_base = count_base.where(IPO.market == market)
    if industry is not None:
        base = base.where(IPO.industry_l1 == industry)
        count_base = count_base.where(IPO.industry_l1 == industry)
    if year_from is not None:
        base = base.where(extract("year", IPO.listing_date) >= year_from)
        count_base = count_base.where(
            extract("year", IPO.listing_date) >= year_from
        )
    if year_to is not None:
        base = base.where(extract("year", IPO.listing_date) <= year_to)
        count_base = count_base.where(extract("year", IPO.listing_date) <= year_to)
    if sponsor is not None:
        # JSONB ``@>`` 整字段匹配: sponsors=['中金公司', ...] 含 ['中金公司'] → True
        base = base.where(IPO.sponsors.contains([sponsor]))
        count_base = count_base.where(IPO.sponsors.contains([sponsor]))

    return base, count_base


@cached(
    ttl_seconds=HISTORICAL_LIST_CACHE_TTL_SECONDS,
    namespace="ipos:historical",
)
async def list_historical_ipos(
    *,
    market: Market | None = None,
    industry: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    sponsor: str | None = None,
    sort_by: HistoricalSortBy = "listing_date",
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """历史 IPO 多维筛选 + 排序 + 分页 (BE-S4-003).

    设计:
    - ``market=None`` 默认全市场 (HK + A); 路由层按需限定 market='HK' 等
    - 排序: 三档枚举走 ``DESC NULLS LAST`` — NULL 行 (上市前 / 缺数据) 永远沉底,
      不污染 "前 25%" 视觉; 同分用 ``code ASC`` 兜底稳定排序避免 page 漂移
    - 路由层 forbid 非 listed 行 (在 query 层 ``IPO.status == 'listed'`` 强制),
      与 ``ix_ipos_status_listing_date`` partial 索引匹配
    - 缓存 600s + JSON-friendly dict 返回, 路由层 ``model_validate``
    """
    factory = get_session_factory()
    base, count_base = _build_historical_query(
        market=market,
        industry=industry,
        year_from=year_from,
        year_to=year_to,
        sponsor=sponsor,
    )

    sort_col_map = {
        "listing_date": IPO.listing_date,
        "first_day_change_pct": IPO.first_day_change_pct,
        "one_lot_winning_rate": IPO.one_lot_winning_rate,
    }
    primary = sort_col_map[sort_by]
    base = (
        base.order_by(primary.desc().nulls_last(), IPO.code.asc())
        .limit(size)
        .offset((page - 1) * size)
    )

    async with factory() as session:
        rows = (await session.execute(base)).scalars().all()
        total = (await session.execute(count_base)).scalar_one()

    items = [_orm_to_historical_item(r) for r in rows]
    market_field: Market | Literal["all"] = market if market else "all"
    payload = HistoricalIPOListResponse(
        items=items,
        total=int(total),
        market=market_field,
        page=page,
        size=size,
        filter_summary={
            "market": market,
            "industry": industry,
            "year_from": year_from,
            "year_to": year_to,
            "sponsor": sponsor,
            "sort_by": sort_by,
        },
    )
    return payload.model_dump(mode="json")


_PEER_STATS_SQL = sa_text("""
SELECT
    count(*) AS peer_count,
    avg(first_day_change_pct)::float AS fd_mean,
    percentile_cont(0.5)
        WITHIN GROUP (ORDER BY first_day_change_pct)::float AS fd_median,
    percentile_cont(0.25)
        WITHIN GROUP (ORDER BY first_day_change_pct)::float AS fd_p25,
    percentile_cont(0.75)
        WITHIN GROUP (ORDER BY first_day_change_pct)::float AS fd_p75,
    min(first_day_change_pct)::float AS fd_min,
    max(first_day_change_pct)::float AS fd_max,
    avg(pe_ratio)::float AS pe_mean,
    percentile_cont(0.5)
        WITHIN GROUP (ORDER BY pe_ratio)::float AS pe_median,
    percentile_cont(0.25)
        WITHIN GROUP (ORDER BY pe_ratio)::float AS pe_p25,
    percentile_cont(0.75)
        WITHIN GROUP (ORDER BY pe_ratio)::float AS pe_p75,
    min(pe_ratio)::float AS pe_min,
    max(pe_ratio)::float AS pe_max,
    avg(one_lot_winning_rate)::float AS wr_mean,
    percentile_cont(0.5)
        WITHIN GROUP (ORDER BY one_lot_winning_rate)::float AS wr_median,
    percentile_cont(0.25)
        WITHIN GROUP (ORDER BY one_lot_winning_rate)::float AS wr_p25,
    percentile_cont(0.75)
        WITHIN GROUP (ORDER BY one_lot_winning_rate)::float AS wr_p75,
    min(one_lot_winning_rate)::float AS wr_min,
    max(one_lot_winning_rate)::float AS wr_max,
    avg(oversubscribe_multiple)::float AS om_mean,
    percentile_cont(0.5)
        WITHIN GROUP (ORDER BY oversubscribe_multiple)::float AS om_median,
    percentile_cont(0.25)
        WITHIN GROUP (ORDER BY oversubscribe_multiple)::float AS om_p25,
    percentile_cont(0.75)
        WITHIN GROUP (ORDER BY oversubscribe_multiple)::float AS om_p75,
    min(oversubscribe_multiple)::float AS om_min,
    max(oversubscribe_multiple)::float AS om_max,
    avg(raised_amount)::float AS ra_mean,
    percentile_cont(0.5)
        WITHIN GROUP (ORDER BY raised_amount)::float AS ra_median,
    percentile_cont(0.25)
        WITHIN GROUP (ORDER BY raised_amount)::float AS ra_p25,
    percentile_cont(0.75)
        WITHIN GROUP (ORDER BY raised_amount)::float AS ra_p75,
    min(raised_amount)::float AS ra_min,
    max(raised_amount)::float AS ra_max
FROM ipos
WHERE status = 'listed'
  AND industry_l1 = :industry_l1
  AND first_day_change_pct IS NOT NULL
""")


def _row_to_stats(row: Any, prefix: str) -> IPOPeerStats:
    """把 percentile SQL 行的 (mean / median / p25 / p75 / min / max) 抽出来.

    PG ``percentile_cont`` 在没有数据时返 NULL — 端层兜成 None.
    """
    return IPOPeerStats(
        mean=getattr(row, f"{prefix}_mean", None),
        median=getattr(row, f"{prefix}_median", None),
        p25=getattr(row, f"{prefix}_p25", None),
        p75=getattr(row, f"{prefix}_p75", None),
        min=getattr(row, f"{prefix}_min", None),
        max=getattr(row, f"{prefix}_max", None),
    )


@cached(
    ttl_seconds=PEER_AGGREGATE_CACHE_TTL_SECONDS,
    namespace="ipo:peer",
)
async def compute_peer_aggregate(code: str) -> dict[str, Any] | None:
    """行业聚合统计 (BE-S4-003 ``/ipos/{code}/peer-aggregate``).

    流程:
    1. 查目标 IPO 的 ``industry_l1`` (没行业 / 不存在 → None, 路由层 404)
    2. 走 ``_PEER_STATS_SQL`` 一次性算 5 维 percentile (PG 原生 percentile_cont)
    3. 查 top 50 dot for 散点图 (按 ``raised_amount DESC NULLS LAST`` 取 top, 含 self)
    4. ``peer_count < 5`` 时 stats 全 None + scatter_points=[] (FE 走"数据不足"分支)

    缓存 600s; force refresh 旁路通过装饰器外部清缓存.
    """
    code_upper = code.upper().strip()
    factory = get_session_factory()

    async with factory() as session:
        # 1. 查目标 IPO 行业
        target_row = (
            await session.execute(
                select(IPO.industry_l1, IPO.market).where(IPO.code == code_upper)
            )
        ).first()
        if target_row is None or target_row.industry_l1 is None:
            return None

        industry_l1 = target_row.industry_l1

        # 2. percentile 一次性算 5 维 (PG percentile_cont WITHIN GROUP)
        stats_row = (
            await session.execute(
                _PEER_STATS_SQL,
                {"industry_l1": industry_l1},
            )
        ).first()

        peer_count = int(stats_row.peer_count) if stats_row else 0

        # 3. < 5 篇兜底: 直接返空 stats
        if peer_count < PEER_AGGREGATE_MIN_SAMPLES:
            payload = IPOPeerAggregate(
                code=code_upper,
                industry_l1=industry_l1,
                peer_count=peer_count,
                first_day_change_pct=IPOPeerStats(),
                pe_ratio=IPOPeerStats(),
                one_lot_winning_rate=IPOPeerStats(),
                oversubscribe_multiple=IPOPeerStats(),
                raised_amount=IPOPeerStats(),
                scatter_points=[],
            )
            return payload.model_dump(mode="json")

        # 4. 散点图 dot: 按 raised_amount DESC 取 top, self 优先 (UNION 写法保证 self 必含)
        scatter_rows = (
            await session.execute(
                select(
                    IPO.code, IPO.name, IPO.pe_ratio, IPO.first_day_change_pct
                )
                .where(
                    IPO.status == "listed",
                    IPO.industry_l1 == industry_l1,
                    IPO.first_day_change_pct.isnot(None),
                )
                .order_by(
                    (IPO.code == code_upper).desc(),  # self 优先
                    IPO.raised_amount.desc().nulls_last(),
                )
                .limit(PEER_SCATTER_MAX_POINTS)
            )
        ).all()
        scatter_points = [
            IPOPeerScatterPoint(
                code=r.code,
                name=r.name,
                pe_ratio=float(r.pe_ratio) if r.pe_ratio is not None else None,
                first_day_change_pct=(
                    float(r.first_day_change_pct)
                    if r.first_day_change_pct is not None
                    else None
                ),
                is_self=r.code == code_upper,
            )
            for r in scatter_rows
        ]

    payload = IPOPeerAggregate(
        code=code_upper,
        industry_l1=industry_l1,
        peer_count=peer_count,
        first_day_change_pct=_row_to_stats(stats_row, "fd"),
        pe_ratio=_row_to_stats(stats_row, "pe"),
        one_lot_winning_rate=_row_to_stats(stats_row, "wr"),
        oversubscribe_multiple=_row_to_stats(stats_row, "om"),
        raised_amount=_row_to_stats(stats_row, "ra"),
        scatter_points=scatter_points,
    )
    return payload.model_dump(mode="json")


__all__ = [
    "list_ipos",
    "get_ipo",
    "get_ipo_detail",
    "list_historical_ipos",
    "compute_peer_aggregate",
    "LIST_CACHE_TTL_SECONDS",
    "DETAIL_CACHE_TTL_SECONDS",
    "HISTORICAL_LIST_CACHE_TTL_SECONDS",
    "PEER_AGGREGATE_CACHE_TTL_SECONDS",
    "PEER_AGGREGATE_MIN_SAMPLES",
    "PEER_SCATTER_MAX_POINTS",
]
