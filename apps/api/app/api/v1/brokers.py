"""券商相关路由 (BE-S3-007 横向对比 API).

- ``GET /brokers``: 列表 + 3 维筛选 (market_support / partnership_type / only_active)
- ``GET /brokers/{slug}``: 详情 by slug (URL 友好, ``/brokers/futubull``)

partnership_* 隔离
==================
service 层返完整 dict (含 partnership_* 三字段), 路由层用 ``to_public_dict``
显式剥掉再 ``BrokerPublic.model_validate``; ``BrokerPublic`` ``extra="forbid"``
做防御 in depth (即便忘记调 helper, 也会 raise 而非偷偷泄漏).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.schemas.broker import (
    BrokerListResponse,
    BrokerPublic,
    to_public_dict,
)
from app.services import broker_service

router = APIRouter(prefix="/brokers", tags=["brokers"])


@router.get(
    "",
    response_model=BrokerListResponse,
    summary="券商列表 (3 维筛选 + display_order DESC 排序)",
)
async def list_brokers(
    market: Annotated[
        broker_service.MarketFilter,
        Query(description="支持市场: HK / A / US / SG / all (默认 all)"),
    ] = "all",
    partnership: Annotated[
        broker_service.PartnershipFilter,
        Query(
            description=(
                "合作类型: CPA / CPS / BOTH / NONE / all; "
                "FE 通常用 all (展示所有券商); 内部运营路由可走 BOTH"
            )
        ),
    ] = "all",
) -> BrokerListResponse:
    """券商列表. 默认隐藏 ``is_active=False`` 的券商 (运营临时下架)."""
    payload = await broker_service.list_brokers(
        market=market, partnership=partnership, only_active=True
    )
    items_public = [
        BrokerPublic.model_validate(to_public_dict(item)) for item in payload["items"]
    ]
    return BrokerListResponse(items=items_public, total=int(payload["total"]))


@router.get(
    "/{slug}",
    response_model=BrokerPublic,
    summary="券商详情 by slug",
    responses={404: {"description": "slug 不存在或券商已下架"}},
)
async def get_broker_detail(slug: str) -> BrokerPublic:
    payload = await broker_service.get_broker_detail(slug)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "broker_not_found",
                "message": f"broker {slug} not found",
            },
        )
    return BrokerPublic.model_validate(to_public_dict(payload))
