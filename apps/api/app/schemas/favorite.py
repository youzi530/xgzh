"""自选股相关 Pydantic 模型 (BE-010).

设计原则:
- ``code`` 由前端**带市场后缀**传 (``0700.HK`` / ``600519.SH`` 等), 后端 ``_parse_code``
  反推 market; 这跟 ``IPOItem.code`` 字段对齐, 让前端只需要持一份 ``code`` 标识即可
  在"列表/详情/收藏"三处共用, 不需要额外维护 ``(code, market)`` 对.
- ``FavoriteItem`` 是"用户自选 row + 该 IPO 的最新行情字段"的 LEFT JOIN 投影:
  当用户收藏的是 HK seed 数据 (尚未入 ``ipos`` 表) 时, 行情字段全 ``None``,
  前端按"占位卡片"渲染.
- ``notify_on_subscribe`` 默认 ``True`` (打新提醒). 后续 BE-011 推送 token 落地后,
  cron 在 IPO ``status`` 进入 ``subscribing`` 时按这个 flag 推单.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_serializer

from app.schemas.ipo import IPOStatus, Market


class FavoriteAddRequest(BaseModel):
    code: str = Field(
        min_length=1,
        max_length=24,
        description="带市场后缀的 IPO code, 如 0700.HK / 600519.SH",
    )
    notify_on_subscribe: bool = Field(
        default=True,
        description="申购窗口打开时是否推送提醒 (BE-011 落地后生效)",
    )


class FavoriteAddResponse(BaseModel):
    ok: bool = True
    code: str = Field(description="规范化后 (大写) 的 code")
    market: Market
    notify_on_subscribe: bool
    favorited_at: datetime
    created: bool = Field(
        description="本次调用是否新增收藏. False = 此前已收藏 (幂等返回 200)"
    )


class FavoriteRemoveResponse(BaseModel):
    ok: bool = True
    code: str
    market: Market
    removed: bool = Field(
        description="True = 真删了一行; False = 本来就没收藏 (幂等返回 200)"
    )


class FavoriteItem(BaseModel):
    """``user_favorites`` ⨝ ``ipos`` (LEFT JOIN) 的扁平化投影."""

    code: str
    market: Market
    notify_on_subscribe: bool
    favorited_at: datetime

    name: str | None = Field(default=None, description="为空意味着 ipos 表中尚无该 code")
    industry: str | None = None
    issue_price: Decimal | None = None
    issue_currency: str | None = None
    listing_date: Date | None = None
    status: IPOStatus = "unknown"
    one_lot_winning_rate: Decimal | None = Field(
        default=None, description="一手中签率 (0-1)"
    )
    data_source: str | None = None

    @field_serializer(
        "issue_price", "one_lot_winning_rate", when_used="json"
    )
    def _ser_decimal(self, v: Decimal | None) -> float | None:
        return float(v) if v is not None else None


class FavoriteListResponse(BaseModel):
    items: list[FavoriteItem]
    total: int = Field(ge=0, description="自选总条数 (= len(items), 当前不分页)")
