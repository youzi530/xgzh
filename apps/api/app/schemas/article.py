"""文章相关 Pydantic 模型 (BE-S3-005 / BE-S3-006).

BE-S3-005: ``TLDRResponse`` 多空汇总返回结构.
BE-S3-006: ``ArticleListItem`` / ``ArticleDetail`` / ``ArticleSearchResult``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer

Scope = Literal["ipo", "market", "custom"]
Sentiment = Literal["bullish", "neutral", "bearish"]
TLDRStatus = Literal["ok", "insufficient_data"]
Market = Literal["HK", "A", "BOTH"]
SortBy = Literal["published_at", "hot_score"]


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


# ─── BE-S3-006: 文章列表 / 详情 / 全文搜索 ─────────────────────────────────


class ArticleListItem(BaseModel):
    """文章列表项 (卡片视图; 列表 API 返回).

    刻意不返回 ``content`` / 长 ``summary`` 全文 — 列表只放卡片字段, 减小 payload;
    用户点进详情页再走 ``GET /articles/{id}`` 拿完整内容.
    """

    model_config = ConfigDict(from_attributes=True)

    article_id: UUID
    title: str
    summary: str | None = Field(default=None, description="100 字 AI 摘要; 可能 NULL")
    source_name: str
    source_logo_url: str | None = None
    source_credibility: int = Field(ge=1, le=3, description="1=低 / 2=中 / 3=高")
    original_url: str
    market: Market
    related_ipos: list[dict[str, Any]] = Field(
        default_factory=list, description="[{code, market, name}, ...]"
    )
    sentiment: Sentiment | None = Field(
        default=None, description="NULL = 还未打标"
    )
    sentiment_score: Decimal | None = Field(default=None, description="-1.000 ~ 1.000")
    keywords: list[str] = Field(default_factory=list)
    hot_score: Decimal = Field(description="热度排序")
    is_full_text_available: bool
    published_at: datetime

    @field_serializer("sentiment_score", "hot_score", when_used="json")
    def _ser_decimal(self, v: Decimal | None) -> float | None:
        return float(v) if v is not None else None


class ArticleListResponse(BaseModel):
    """``GET /api/v1/articles`` 返回结构."""

    items: list[ArticleListItem]
    total: int = Field(ge=0, description="当前 query 命中的总条数 (不分页前)")
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=50)


class ArticleDetail(ArticleListItem):
    """文章详情 (在列表项基础上叠加同主题相关文章列表).

    ``related_articles``: 走 ``article_topics.parent_article_id = self.article_id``
    反查的 child 列表 (BE-S3-003 的副产品), 前端展"主文 + N 篇相关报道".
    """

    related_articles: list[ArticleListItem] = Field(
        default_factory=list,
        description="同 topic 折叠的 child 列表 (BE-S3-003 dedup 链); 主文专属",
    )


class ArticleSearchHit(ArticleListItem):
    """全文搜索单条结果. 在列表字段基础上加 ``rank`` (PG ts_rank_cd 输出)."""

    rank: float = Field(description="PG ts_rank_cd 相关度分; 越大越相关")


class ArticleSearchResponse(BaseModel):
    """``GET /api/v1/search/articles?q=xxx`` 返回结构."""

    items: list[ArticleSearchHit]
    total: int = Field(ge=0, description="命中总数 (不分页前)")
    query: str = Field(description="原始 query (回显, 便于前端高亮)")
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=50)
