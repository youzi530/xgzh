"""新股相关 Pydantic 模型（参见 spec/03 §1.3 Article-like 字段约定）."""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer

Market = Literal["HK", "A", "US"]
IPOStatus = Literal["upcoming", "subscribing", "listed", "withdrawn", "unknown"]
# Sprint 4 BE-S4-003 历史 IPO 排序枚举: 列表页"按时间 / 按首日涨幅 / 按中签率"
HistoricalSortBy = Literal[
    "listing_date", "first_day_change_pct", "one_lot_winning_rate"
]


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
    # BUG-S6.7-002: 招股股数 (股) — 用户决策核心字段, 与 ``raised_amount`` 配对.
    # 走 ``ipos.extra.total_shares`` JSONB 旁路 (与 ``highlights`` / ``risks`` 同款),
    # 0 alembic 迁移. ingest 阶段 :func:`eastmoney_ipo_client.parse_eastmoney_ipo_html`
    # 从 ``"4262.68万"`` / ``"5854.82万"`` / ``"-"`` 解析 → Decimal 股数.
    total_shares: Decimal | None = Field(
        default=None,
        description="招股股数 (单位: 股); 来自东方财富 ipolist 列表页",
    )
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

    @field_serializer("total_shares", when_used="json")
    def _ser_total_shares(self, v: Decimal | None) -> float | None:
        return float(v) if v is not None else None


# ─── Sprint 4 BE-S4-003: 历史 IPO 列表 + 行业聚合 ──────────────────


class HistoricalIPOItem(IPOItem):
    """历史 IPO 列表卡片 (BE-S4-003): IPOItem + 3 个上市后回填字段 + sponsors.

    与 ``IPOItem`` 区别: 历史列表只展示 ``status='listed'`` 的行, 因此 ``status``
    字段固定 'listed'; ``one_lot_winning_rate`` 上抬到 schema 顶层 (而非 ``extra``),
    与新加的 ``first_day_change_pct`` / ``oversubscribe_multiple`` 一致, 让前端
    一次性拿到"上市后表现"全维度.
    """

    industry_l2: str | None = Field(default=None, description="二级行业 (如 '电商平台')")
    first_day_change_pct: Decimal | None = Field(
        default=None, description="上市首日涨跌幅 % (HK/A 通用)"
    )
    oversubscribe_multiple: Decimal | None = Field(
        default=None, description="公开认购超额倍数 (HK 专用; 285.6 = 285.6 倍)"
    )
    sponsors: list[str] | None = Field(default=None, description="保荐人 / 主承销商列表")

    @field_serializer(
        "first_day_change_pct", "oversubscribe_multiple",
        when_used="json",
    )
    def _ser_extra_decimal(self, v: Decimal | None) -> float | None:
        return float(v) if v is not None else None


class HistoricalIPOListResponse(BaseModel):
    """历史 IPO 列表响应 (BE-S4-003)."""

    items: list[HistoricalIPOItem]
    total: int
    market: Market | Literal["all"]
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=50)
    filter_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="本次查询的筛选条件回显 (前端 URL 同步 / 调试用)",
    )


class IPOPeerStats(BaseModel):
    """单个数值维度的统计聚合 (mean / median / p25 / p75 / min / max).

    ``peer_count < 5`` 时 service 层兜底填全 None, 让前端 uCharts 走"数据不足"分支.
    """

    mean: float | None = None
    median: float | None = None
    p25: float | None = None
    p75: float | None = None
    min: float | None = None
    max: float | None = None


class IPOPeerScatterPoint(BaseModel):
    """uCharts 散点图单个 dot (BE-S4-003 / FE-S4-002).

    ``is_self=True``: 当前 IPO 在散点图中高亮显示 (颜色 + emoji).
    """

    code: str
    name: str
    pe_ratio: float | None = None
    first_day_change_pct: float | None = None
    is_self: bool = False


class IPOPeerAggregate(BaseModel):
    """行业聚合统计响应 (BE-S4-003 ``GET /ipos/{code}/peer-aggregate``).

    给 FE-S4-002 散点图 + 雷达图同时服务, 一次请求拿全 5 维统计 + 散点 dot 列表.
    """

    code: str = Field(description="目标 IPO 代码")
    industry_l1: str | None = Field(default=None, description="一级行业")
    peer_count: int = Field(description="同行业 listed IPO 总数 (含目标 IPO 自身)")
    first_day_change_pct: IPOPeerStats
    pe_ratio: IPOPeerStats
    one_lot_winning_rate: IPOPeerStats = Field(
        default_factory=IPOPeerStats,
        description="HK 专用; A 股全 None",
    )
    oversubscribe_multiple: IPOPeerStats = Field(
        default_factory=IPOPeerStats,
        description="HK 专用; A 股全 None",
    )
    raised_amount: IPOPeerStats = Field(
        default_factory=IPOPeerStats,
        description="募资规模; HK 单位 HKD, A 单位 CNY (前端按 issue_currency 区分)",
    )
    scatter_points: list[IPOPeerScatterPoint] = Field(
        default_factory=list,
        description="散点图 dot 列表 (≤ 50; 含目标 IPO 自身; peer_count<5 时返空)",
    )
