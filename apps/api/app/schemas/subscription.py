"""中签记账域 Pydantic schemas (Sprint 6 BE-S6-002).

字段语义对齐 ``app.db.models.subscription`` ORM + spec/13 §BE-S6-002.

API 矩阵
========
账户:
- ``POST /api/v1/subscriptions/accounts``        创建账户 → ``SubscriptionAccountResponse``
- ``GET  /api/v1/subscriptions/accounts``        列账户 → ``SubscriptionAccountListResponse``
- ``PUT  /api/v1/subscriptions/accounts/{id}``   改账户 → ``SubscriptionAccountResponse``
- ``DELETE /api/v1/subscriptions/accounts/{id}`` 删账户 → 204

中签 records:
- ``POST   /api/v1/subscriptions``               录中签 → ``SubscriptionRecordResponse``
- ``GET    /api/v1/subscriptions``               列中签 → ``SubscriptionRecordListResponse``
- ``GET    /api/v1/subscriptions/{id}``          详情 → ``SubscriptionRecordResponse``
- ``PUT    /api/v1/subscriptions/{id}``          改 → ``SubscriptionRecordResponse``
- ``DELETE /api/v1/subscriptions/{id}``          删 → 204
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 与 ORM region check 对齐 ('HK' / 'CN' / 'US')
SubscriptionRegion = Literal["HK", "CN", "US"]


# ─── 账户 (SubscriptionAccount) ─────────────────────────────────────────


class SubscriptionAccountCreateRequest(BaseModel):
    """``POST /api/v1/subscriptions/accounts`` 请求体."""

    label: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="账户名 (32 char 上限), 如 '招商' / '华盛'",
    )
    broker_name: str | None = Field(
        default=None,
        max_length=32,
        description="optional 真券商名 ('招商证券')",
    )
    region: SubscriptionRegion = Field(
        default="HK",
        description="HK / CN / US",
    )
    is_primary: bool = Field(
        default=False,
        description="是否设为主账户; 切换器默认选中",
    )

    @field_validator("label")
    @classmethod
    def _strip_label(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("label 不能全为空白")
        return s


class SubscriptionAccountUpdateRequest(BaseModel):
    """``PUT /api/v1/subscriptions/accounts/{id}`` 请求体 (partial)."""

    label: str | None = Field(default=None, min_length=1, max_length=32)
    broker_name: str | None = Field(default=None, max_length=32)
    region: SubscriptionRegion | None = None
    is_primary: bool | None = None

    @field_validator("label")
    @classmethod
    def _strip_label(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip()
        if not s:
            raise ValueError("label 不能全为空白")
        return s


class SubscriptionAccountResponse(BaseModel):
    """账户详情响应 (从 ORM 直接映射)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    broker_name: str | None
    region: SubscriptionRegion
    is_primary: bool
    created_at: datetime


class SubscriptionAccountListResponse(BaseModel):
    """账户列表响应 (无分页 — 单用户账户数量级 < 10)."""

    items: list[SubscriptionAccountResponse]
    total: int


# ─── 中签 records (SubscriptionRecord) ──────────────────────────────────


class SubscriptionRecordCreateRequest(BaseModel):
    """``POST /api/v1/subscriptions`` 请求体.

    字段联动 (BE 处理):
    - ``ipo_code`` 查 ipos 表回填 ``ipo_name`` / ``listed_at`` / ``first_day_close``
    - 用户传 ``ipo_name`` / ``first_day_close`` 时优先用用户值 (兜底)
    - PnL 由 BE 算后存盘
    """

    account_id: uuid.UUID = Field(..., description="所属账户 (必须属于本人)")
    ipo_code: str = Field(
        ...,
        min_length=1,
        max_length=16,
        description="如 '00700' / '688123'",
    )
    ipo_name: str | None = Field(
        default=None,
        max_length=64,
        description="冗余兜底; ipos 命中时 BE 自动取最新, 用户值优先",
    )
    region: SubscriptionRegion = Field(..., description="必填; 与 account.region 一致")
    subscribe_shares: int = Field(
        ...,
        ge=1,
        description="申购股数, ≥ 1",
    )
    allotted_shares: int = Field(
        default=0,
        ge=0,
        description="中签股数; 0 = 未中签",
    )
    subscribe_price: Decimal | None = Field(default=None, ge=Decimal("0"))
    margin_amount: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        description="港股孖展利息 (A 股留空)",
    )
    fees: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    first_day_close: Decimal | None = Field(default=None, ge=Decimal("0"))
    sell_price: Decimal | None = Field(default=None, ge=Decimal("0"))
    sell_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)
    subscribed_at: date = Field(..., description="申购日期")
    listed_at: date | None = Field(
        default=None,
        description="上市日期; ipos 命中时 BE 自动取最新, 用户值优先",
    )

    @field_validator("ipo_code")
    @classmethod
    def _normalize_code(cls, v: str) -> str:
        """ipo_code 大小写归一化 + 去前导零保留 (港股 '00700' 与 '700' 不同位)"""
        return v.strip().upper()

    @field_validator("allotted_shares")
    @classmethod
    def _check_allotted_le_subscribe(cls, v: int, info: object) -> int:
        # info.data 拿到已 validated 字段; subscribe_shares 在前已校验
        data = getattr(info, "data", {}) or {}
        sub = data.get("subscribe_shares")
        if sub is not None and v > sub:
            raise ValueError(
                f"allotted_shares ({v}) 不能大于 subscribe_shares ({sub})"
            )
        return v


class SubscriptionRecordUpdateRequest(BaseModel):
    """``PUT /api/v1/subscriptions/{id}`` 请求体 (partial; 任一字段可单独改)."""

    account_id: uuid.UUID | None = None
    ipo_code: str | None = Field(default=None, min_length=1, max_length=16)
    ipo_name: str | None = Field(default=None, max_length=64)
    region: SubscriptionRegion | None = None
    subscribe_shares: int | None = Field(default=None, ge=1)
    allotted_shares: int | None = Field(default=None, ge=0)
    subscribe_price: Decimal | None = Field(default=None, ge=Decimal("0"))
    margin_amount: Decimal | None = Field(default=None, ge=Decimal("0"))
    fees: Decimal | None = Field(default=None, ge=Decimal("0"))
    first_day_close: Decimal | None = Field(default=None, ge=Decimal("0"))
    sell_price: Decimal | None = Field(default=None, ge=Decimal("0"))
    sell_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)
    subscribed_at: date | None = None
    listed_at: date | None = None

    @field_validator("ipo_code")
    @classmethod
    def _normalize_code(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip().upper()


class SubscriptionRecordResponse(BaseModel):
    """单条中签详情响应 (含计算后的 PnL)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    ipo_code: str
    ipo_name: str | None
    region: SubscriptionRegion
    subscribe_shares: int
    allotted_shares: int
    subscribe_price: Decimal | None
    margin_amount: Decimal | None
    fees: Decimal
    first_day_close: Decimal | None
    sell_price: Decimal | None
    sell_at: datetime | None
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    notes: str | None
    subscribed_at: date
    listed_at: date | None
    created_at: datetime
    updated_at: datetime


class SubscriptionRecordListResponse(BaseModel):
    """``GET /api/v1/subscriptions`` 分页响应."""

    items: list[SubscriptionRecordResponse]
    total: int = Field(..., description="符合 filter 条件的总数")
    limit: int
    offset: int


# ─── 汇总 (BE-S6-003) ───────────────────────────────────────────────────


SubscriptionSummaryGroupBy = Literal["month", "year", "ipo"]


class SubscriptionSummaryGroup(BaseModel):
    """单个分组桶: key='2026-04' / '2026' / '00700'."""

    key: str = Field(..., description="分组键: month=YYYY-MM, year=YYYY, ipo=ipo_code")
    label: str = Field(..., description="UI 显示用的人话")
    count: int = Field(..., description="本组总记录条数")
    allotted_count: int = Field(..., description="本组中签条数 (allotted_shares > 0)")
    realized_pnl: Decimal | None = Field(
        default=None, description="本组已实现 PnL 求和; 全部 NULL 时仍为 NULL"
    )
    unrealized_pnl: Decimal | None = Field(
        default=None, description="本组浮盈浮亏求和; 全部 NULL 时仍为 NULL"
    )


class SubscriptionSummaryResponse(BaseModel):
    """``GET /api/v1/subscriptions/summary`` 响应."""

    group_by: SubscriptionSummaryGroupBy
    groups: list[SubscriptionSummaryGroup] = Field(
        ..., description="分组列表 (按 key 倒序; ipo 按 PnL 倒序)"
    )
    total: SubscriptionSummaryGroup = Field(
        ..., description="全 user 维度合计 (key='_total')"
    )


__all__ = [
    "SubscriptionAccountCreateRequest",
    "SubscriptionAccountListResponse",
    "SubscriptionAccountResponse",
    "SubscriptionAccountUpdateRequest",
    "SubscriptionRecordCreateRequest",
    "SubscriptionRecordListResponse",
    "SubscriptionRecordResponse",
    "SubscriptionRecordUpdateRequest",
    "SubscriptionRegion",
    "SubscriptionSummaryGroup",
    "SubscriptionSummaryGroupBy",
    "SubscriptionSummaryResponse",
]
