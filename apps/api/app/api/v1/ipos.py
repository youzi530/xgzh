"""新股相关路由 (spec/03 §1)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.schemas.ipo import IPOItem, IPOListResponse, Market
from app.services import ipo_service

router = APIRouter(prefix="/ipos", tags=["ipos"])


@router.get("", response_model=IPOListResponse)
async def list_ipos(
    market: Annotated[Market, Query(description="市场: HK / A / US")] = "HK",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> IPOListResponse:
    items = await ipo_service.list_ipos(market=market, limit=limit)
    return IPOListResponse(items=items, total=len(items), market=market)


@router.get("/{code}", response_model=IPOItem)
async def get_ipo(code: str) -> IPOItem:
    item = await ipo_service.get_ipo(code)
    if item is None:
        raise HTTPException(status_code=404, detail=f"IPO {code} not found")
    return item
