"""VIP 域 Pydantic 模型 (BE-S3-009).

3 个面向客户端的响应:
- ``MembershipResponse``  ← ``GET /vip/me``     (查自己的订阅状态)
- ``OrderResponse``       ← ``GET /vip/orders`` (订单列表项)
- ``OrdersListResponse``  ← ``GET /vip/orders`` (订单列表 + 分页元信息)

ORM 字段 → Schema 字段 100% 对齐 (大小写 + 命名), 不做 alias / 缩写,
避免前端 / 后端各自猜命名 (减少跨端 contract bug).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PlanLiteral = Literal["trial", "monthly", "quarterly", "yearly", "lifetime"]
StatusLiteral = Literal["trialing", "active", "expired", "cancelled"]
PaymentChannelLiteral = Literal[
    "wechat_mp", "wechat_h5", "apple_iap", "internal"
]
OrderStatusLiteral = Literal["pending", "paid", "failed", "refunded"]


class MembershipResponse(BaseModel):
    """``GET /vip/me`` 返回; 当前用户订阅信息.

    用户从未付费 / 注册但 ``vip_trial_days=0`` 时返回所有字段为 None 的"伪 membership".
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    has_active: bool = Field(
        description="是否当前生效 (status active/trialing + end_at > now). False 时其它字段仍可能有值 (历史订阅信息)."
    )
    membership_id: uuid.UUID | None = Field(
        default=None, description="None 表示用户从未有过订阅记录"
    )
    user_id: uuid.UUID
    status: StatusLiteral | None = Field(default=None)
    plan: PlanLiteral | None = Field(default=None)
    start_at: datetime | None = Field(default=None)
    end_at: datetime | None = Field(default=None)
    auto_renew: bool = Field(default=False)
    total_paid_cny: Decimal = Field(
        default=Decimal("0"),
        description="累计支付 CNY; 试用期间 = 0; 续费时累加 (财务对账依据)",
    )
    days_remaining: int | None = Field(
        default=None,
        ge=0,
        description="距离 end_at 剩余天数 (向下取整). lifetime 返 36500+ 大数; expired 返 None.",
    )


class OrderResponse(BaseModel):
    """``GET /vip/orders`` 列表项.

    ``raw_callback`` (微信回调原始 payload) 不暴露给客户端 (PII + 商户敏感数据).
    """

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    order_id: uuid.UUID
    out_trade_no: str
    plan: PlanLiteral
    amount_cny: Decimal
    status: OrderStatusLiteral
    payment_channel: PaymentChannelLiteral
    transaction_id: str | None = None
    paid_at: datetime | None = None
    created_at: datetime


class OrdersListResponse(BaseModel):
    """``GET /vip/orders`` 顶层返回 (含 limit + total 元数据).

    暂不分页 (用户订单 < 100 条; lifetime 上限 ≤ 20 笔), 直接返全量.
    """

    model_config = ConfigDict(extra="forbid")

    items: list[OrderResponse]
    total: int = Field(ge=0, description="返回项数 = items 长度 (一次取全)")


__all__ = [
    "MembershipResponse",
    "OrderResponse",
    "OrdersListResponse",
    "OrderStatusLiteral",
    "PaymentChannelLiteral",
    "PlanLiteral",
    "StatusLiteral",
]
