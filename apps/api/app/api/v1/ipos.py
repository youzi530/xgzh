"""新股相关路由 (spec/03 §1).

BE-008: ``GET /ipos`` 切回数据库, 增加 ``status`` / ``industry`` 筛选 + 分页.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.schemas.ipo import IPOItem, IPOListResponse, IPOStatus, Market
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


@router.get("/{code}", response_model=IPOItem, summary="新股详情 (按 code 精确查)")
async def get_ipo(code: str) -> IPOItem:
    item = await ipo_service.get_ipo(code)
    if item is None:
        raise HTTPException(status_code=404, detail=f"IPO {code} not found")
    return item
