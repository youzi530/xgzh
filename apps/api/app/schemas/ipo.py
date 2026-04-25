"""新股相关 Pydantic 模型（参见 spec/03 §1.3 Article-like 字段约定）."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_serializer

Market = Literal["HK", "A", "US"]
IPOStatus = Literal["upcoming", "subscribing", "listed", "withdrawn", "unknown"]


class IPOItem(BaseModel):
    """新股列表 / 详情通用字段."""

    code: str = Field(description="带市场后缀, 如 0700.HK / 600519.SH")
    name: str
    market: Market
    industry: str | None = None
    issue_price: Decimal | None = Field(default=None, description="发行价")
    issue_currency: str | None = Field(default=None, description="ISO 4217, e.g. HKD/CNY")
    listing_date: Date | None = None
    subscribe_start: datetime | None = None
    subscribe_end: datetime | None = None
    pe_ratio: Decimal | None = None
    raised_amount: Decimal | None = None
    one_lot_winning_rate: Decimal | None = Field(
        default=None, description="一手中签率 (0-1)"
    )
    status: IPOStatus = "unknown"
    data_source: str = ""
    updated_at: datetime | None = None

    @field_serializer(
        "issue_price", "pe_ratio", "raised_amount", "one_lot_winning_rate",
        when_used="json",
    )
    def _ser_decimal(self, v: Decimal | None) -> float | None:
        return float(v) if v is not None else None


class IPOListResponse(BaseModel):
    items: list[IPOItem]
    total: int
    market: Market
