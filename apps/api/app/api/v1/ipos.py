"""新股相关路由 (spec/03 §1).

BE-008: ``GET /ipos`` 切回数据库, 增加 ``status`` / ``industry`` 筛选 + 分页.
BE-S4-003: ``GET /ipos/historical`` 多维筛选 + ``GET /ipos/{code}/peer-aggregate`` 行业聚合
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.schemas.ipo import (
    HistoricalIPOListResponse,
    HistoricalSortBy,
    IPODetail,
    IPOListResponse,
    IPOPeerAggregate,
    IPOStatus,
    Market,
)
from app.services import ipo_service

router = APIRouter(prefix="/ipos", tags=["ipos"])


@router.get(
    "",
    response_model=IPOListResponse,
    summary="新股列表 (按市场 + 筛选 + 分页)",
)
async def list_ipos(
    market: Annotated[Market, Query(description="市场: HK / A / US")] = "HK",
    status: Annotated[
        IPOStatus | None,
        Query(description="筛选状态: upcoming / subscribing / listed / withdrawn"),
    ] = None,
    industry: Annotated[
        str | None, Query(description="行业一级分类精确匹配 (industry_l1)")
    ] = None,
    page: Annotated[int, Query(ge=1, description="页码, 1-based")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="每页条数")] = 20,
) -> IPOListResponse:
    payload = await ipo_service.list_ipos(
        market=market,
        status=status,
        industry=industry,
        page=page,
        size=size,
    )
    return IPOListResponse.model_validate(payload)


# ─── BE-S4-003 历史 IPO 路由 (放在 /{code} 之前避免路径歧义) ─────


@router.get(
    "/historical",
    response_model=HistoricalIPOListResponse,
    summary="历史 IPO 列表 (BE-S4-003: 多维筛选 + 排序 + 分页)",
)
async def list_historical_ipos(
    market: Annotated[
        Market | None,
        Query(description="市场: HK / A; 不传则全市场 (港 + A 合并)"),
    ] = None,
    industry: Annotated[
        str | None,
        Query(description="一级行业精确匹配 (industry_l1)"),
    ] = None,
    year_from: Annotated[
        int | None,
        Query(ge=1990, le=2100, description="起始年份 (含, 按 listing_date 年)"),
    ] = None,
    year_to: Annotated[
        int | None,
        Query(ge=1990, le=2100, description="结束年份 (含)"),
    ] = None,
    sponsor: Annotated[
        str | None,
        Query(description="保荐人 / 主承销商精确匹配 (JSONB 数组元素)"),
    ] = None,
    sort_by: Annotated[
        HistoricalSortBy,
        Query(description="排序: listing_date / first_day_change_pct / one_lot_winning_rate"),
    ] = "listing_date",
    page: Annotated[int, Query(ge=1, description="页码, 1-based")] = 1,
    size: Annotated[int, Query(ge=1, le=50, description="每页条数, 上限 50")] = 20,
) -> HistoricalIPOListResponse:
    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_year_range",
                "message": f"year_from={year_from} 不能大于 year_to={year_to}",
            },
        )
    payload = await ipo_service.list_historical_ipos(
        market=market,
        industry=industry,
        year_from=year_from,
        year_to=year_to,
        sponsor=sponsor,
        sort_by=sort_by,
        page=page,
        size=size,
    )
    return HistoricalIPOListResponse.model_validate(payload)


@router.get(
    "/{code}/peer-aggregate",
    response_model=IPOPeerAggregate,
    summary="行业聚合统计 (BE-S4-003: percentile + 散点图)",
    responses={
        404: {"description": "code 不存在 / 没行业信息"},
    },
)
async def get_peer_aggregate(code: str) -> IPOPeerAggregate:
    """同行业历史 IPO 的 5 维统计 (mean/median/p25/p75/min/max) + 散点图.

    ``peer_count < 5`` 时 stats 全 None + scatter_points=[] (FE 走"数据不足"分支).
    """
    payload = await ipo_service.compute_peer_aggregate(code)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "ipo_or_industry_missing",
                "message": f"IPO {code} 不存在 / 没行业信息",
            },
        )
    return IPOPeerAggregate.model_validate(payload)


@router.get(
    "/{code}",
    response_model=IPODetail,
    summary="新股详情 (BE-009: 多源字段聚合 + 30min 缓存)",
    responses={404: {"description": "code 不存在或还未抓到"}},
)
async def get_ipo_detail(code: str) -> IPODetail:
    payload = await ipo_service.get_ipo_detail(code)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "ipo_not_found",
                "message": f"IPO {code} not found",
            },
        )
    return IPODetail.model_validate(payload)
