"""知识库域 Pydantic schemas (Sprint 6 BE-S6-004).

API 矩阵
========
- ``GET /api/v1/knowledge?category=hk|cn|general&level=1&tag=入门&page=1&page_size=20``
  → ``KnowledgeListResponse``
- ``GET /api/v1/knowledge/categories`` → ``KnowledgeCategoriesResponse`` (分类 + 各类计数)
- ``GET /api/v1/knowledge/{slug}`` → ``KnowledgeArticleDetail`` (含 content_md + toc_json,
  详情接口异步 view_count++)

list 端点出于"列表轻量"考量, 不返回 ``content_md`` (markdown 通常 几 KB 一篇) 与
``toc_json`` (上万字节). 详情接口返回完整字段.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

KnowledgeCategory = Literal["hk", "cn", "general"]
KnowledgeSource = Literal["curated", "crawled", "ai-generated"]


class KnowledgeArticleSummary(BaseModel):
    """列表项 (轻量, 不带 content_md / toc_json)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    category: KnowledgeCategory
    tags: list[str] | None
    level: int
    view_count: int
    source: KnowledgeSource
    created_at: datetime
    updated_at: datetime


class KnowledgeArticleDetail(BaseModel):
    """详情 (完整字段)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    category: KnowledgeCategory
    tags: list[str] | None
    level: int
    content_md: str
    toc_json: list[dict[str, Any]] | None
    view_count: int
    is_published: bool
    source: KnowledgeSource
    source_url: str | None
    legal_disclaimer: str | None
    created_at: datetime
    updated_at: datetime


class KnowledgeListResponse(BaseModel):
    items: list[KnowledgeArticleSummary]
    total: int
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


class KnowledgeCategoryItem(BaseModel):
    """``GET /knowledge/categories`` 单个分类项 (含计数)."""

    category: KnowledgeCategory
    label: str = Field(..., description="UI 中文名 (港股 / A 股 / 通用)")
    count: int = Field(..., description="该分类已发布文章数 (is_published=TRUE)")


class KnowledgeCategoriesResponse(BaseModel):
    items: list[KnowledgeCategoryItem]
    total: int = Field(..., description="所有已发布文章总数 (跨分类合计)")


__all__ = [
    "KnowledgeArticleDetail",
    "KnowledgeArticleSummary",
    "KnowledgeCategoriesResponse",
    "KnowledgeCategory",
    "KnowledgeCategoryItem",
    "KnowledgeListResponse",
    "KnowledgeSource",
]
