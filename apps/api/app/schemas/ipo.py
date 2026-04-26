"""新股相关 Pydantic 模型（参见 spec/03 §1.3 Article-like 字段约定）."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

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
    total: int = Field(description="当前 query 命中的总条数 (不分页前)")
    market: Market
    page: int = Field(default=1, ge=1, description="当前页, 1-based")
    size: int = Field(default=20, ge=1, le=100, description="每页条数")


class IPODetail(IPOItem):
    """新股详情 (BE-009): 在 ``IPOItem`` 基础上叠加只在详情页用的字段.

    设计原则:
    - 列表 (BE-008) 用 ``IPOItem`` 拿"卡片信息", 详情 (BE-009) 用 ``IPODetail`` 拿
      "深度信息"; 客户端可以用同一份 store 缓存 list, 详情接口只补 delta.
    - ``highlights`` / ``risks`` 字段第一刀允许为空: 由后续 BE-018 (招股书 RAG) 填,
      Sprint 1 阶段从 ``ipos.extra`` JSONB 中读 (运营或脚本可手动补).
    - ``financial_summary`` 同上, 后续接入 AKShare 财务接口或招股书摘要.
    - ``extra`` 不直接 expose 给客户端, schema 里专门挑出已结构化的字段.
    """

    prospectus_url: str | None = Field(default=None, description="招股书 PDF 链接")
    sponsors: list[str] | None = Field(default=None, description="保荐人 (港股) / 主承销商 (A 股)")
    underwriters: list[str] | None = Field(default=None, description="承销商联席名单")
    highlights: list[str] = Field(
        default_factory=list,
        description="投资亮点要点 (BE-018 RAG 摘要; 当前从 extra.highlights 读)",
    )
    risks: list[str] = Field(
        default_factory=list,
        description="主要风险点 (BE-018 RAG 摘要; 当前从 extra.risks 读)",
    )
    financial_summary: dict[str, Any] | None = Field(
        default=None,
        description="财务摘要 (revenue / net_profit / gross_margin 等), 后续接 AKShare",
    )
