"""文章相关 Pydantic 模型 (BE-S3-005 / BE-S3-006).

BE-S3-005: ``TLDRResponse`` 多空汇总返回结构.
BE-S3-006 (后续): ArticleListItem / ArticleDetail / ArticleSearchResult 待补.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Scope = Literal["ipo", "market", "custom"]
Sentiment = Literal["bullish", "neutral", "bearish"]
TLDRStatus = Literal["ok", "insufficient_data"]


class TLDRResponse(BaseModel):
    """``POST /api/v1/articles/tldr`` 返回结构.

    - ``status="ok"``: 池 ≥ 3 篇, LLM (或统计兜底) 已生成多空汇总
    - ``status="insufficient_data"``: 池 < 3 篇, 前端展 spec/03 §模块二"首屏关怀"文案
    """

    model_config = ConfigDict(extra="forbid")

    status: TLDRStatus = Field(description="ok = 正常生成; insufficient_data = 池太小")
    scope: Scope
    scope_value: str
    article_count: int = Field(ge=0, description="候选池实际命中文章数")

    bullish_ratio: float = Field(
        ge=0.0, le=1.0, description="看多文章占比 (三 ratio 和 ≈ 1.0)"
    )
    neutral_ratio: float = Field(ge=0.0, le=1.0)
    bearish_ratio: float = Field(ge=0.0, le=1.0)

    bullish_points: list[str] = Field(
        default_factory=list,
        description="≤ 3 条看多论据 (具体事实, 单条 ≤ 60 字)",
    )
    bearish_points: list[str] = Field(
        default_factory=list, description="≤ 3 条看空论据"
    )
    source_article_ids: list[str] = Field(
        default_factory=list,
        description="LLM 引用的文章 id; insufficient_data 时仍回填全候选池 id",
    )

    generated_at: datetime
    message: str = Field(
        description="人类可读说明 + 末尾免责声明 (端层 ensure_disclaimer 兜底)"
    )


class TLDRRequest(BaseModel):
    """``POST /api/v1/articles/tldr`` 请求体."""

    model_config = ConfigDict(extra="forbid")

    scope: Scope = Field(description="ipo / market / custom")
    scope_value: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "scope=ipo: IPO code (e.g. 00700.HK); "
            "scope=market: HK / A; "
            "scope=custom: 自由关键词 (走 PG tsvector)"
        ),
    )
    force_refresh: bool = Field(
        default=False,
        description="跳过 Redis 缓存强制重新生成. 默认 false 命中缓存即返",
    )
