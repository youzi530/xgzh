"""VIP 相关只读路由 (BE-S3-009).

- ``GET /vip/me``      → 当前用户的订阅状态 (auth required)
- ``GET /vip/orders``  → 当前用户订单历史最近 N 条 (auth required)

下单 / 回调路径放 ``app/api/v1/payment.py`` (BE-S3-010), 与 vip 域分离 — vip
是"会员状态"读路径, payment 是"商业化交易"写路径.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.db.models import User
from app.schemas.vip import (
    MembershipResponse,
    OrderResponse,
    OrdersListResponse,
)
from app.security.deps import get_current_user
from app.services import vip_service

router = APIRouter(prefix="/vip", tags=["vip"])


_LIFETIME_DAYS_THRESHOLD = 365 * 100  # 100 年; 超过视为 lifetime 合理上界


def _compute_days_remaining(end_at: datetime) -> int:
    """``end_at - now`` 向下取整到天 (UI 友好显示).

    lifetime ``end_at=9999-12-31`` 计算后 > 100 年 = 36500+ 天, 不会溢出 int.
    """
    delta = end_at - datetime.now(UTC)
    if delta.total_seconds() <= 0:
        return 0
    return max(0, math.floor(delta.total_seconds() / 86400))


@router.get(
    "/me",
    response_model=MembershipResponse,
    summary="当前用户订阅状态",
)
async def get_my_membership(
    user: Annotated[User, Depends(get_current_user)],
) -> MembershipResponse:
    """返回当前用户订阅状态 + 剩余天数.

    设计:
    - 用 ``vip_service.get_active_membership`` 拿生效订阅; 没有时降级查"任意状态"
      展示历史信息 (前端用来决定"重新订阅 / 延期" CTA)
    - ``days_remaining`` 仅 ``has_active`` 时填; expired/cancelled 返 None

    端到端 < 5ms (单点查 user_id UNIQUE 索引).
    """
    snapshot = await vip_service.get_active_membership(user.user_id)
    if snapshot is not None:
        return MembershipResponse(
            has_active=True,
            membership_id=snapshot.membership_id,
            user_id=snapshot.user_id,
            status=snapshot.status,  # type: ignore[arg-type]
            plan=snapshot.plan,  # type: ignore[arg-type]
            start_at=snapshot.start_at,
            end_at=snapshot.end_at,
            auto_renew=snapshot.auto_renew,
            total_paid_cny=snapshot.total_paid_cny,
            days_remaining=_compute_days_remaining(snapshot.end_at),
        )

    # 没有 active membership: 拉历史信息 (任意 status) 给前端展示
    historical = await vip_service.get_any_membership(user.user_id)
    if historical is not None:
        return MembershipResponse(
            has_active=False,
            membership_id=historical.membership_id,
            user_id=historical.user_id,
            status=historical.status,  # type: ignore[arg-type]
            plan=historical.plan,  # type: ignore[arg-type]
            start_at=historical.start_at,
            end_at=historical.end_at,
            auto_renew=historical.auto_renew,
            total_paid_cny=historical.total_paid_cny,
            days_remaining=None,
        )

    # 完全没有订阅记录 (注册时 vip_trial_days=0 + 从未付费)
    return MembershipResponse(
        has_active=False,
        membership_id=None,
        user_id=user.user_id,
        status=None,
        plan=None,
        start_at=None,
        end_at=None,
        auto_renew=False,
        total_paid_cny=Decimal("0"),
        days_remaining=None,
    )


@router.get(
    "/orders",
    response_model=OrdersListResponse,
    summary="当前用户订单历史",
)
async def list_my_orders(
    user: Annotated[User, Depends(get_current_user)],
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="返回最近 N 条订单, 默认 20, 最大 100"),
    ] = 20,
) -> OrdersListResponse:
    """订单倒序 (created_at DESC), 走 ``ix_vip_orders_user_created`` 索引.

    spec 注释: 不分页, 一次返全; 量大场景 (lifetime + 长期续费用户) 也 ≤ 100 笔.
    """
    rows = await vip_service.list_user_orders(user.user_id, limit=limit)
    items = [OrderResponse.model_validate(o) for o in rows]
    return OrdersListResponse(items=items, total=len(items))


__all__ = ["router"]
