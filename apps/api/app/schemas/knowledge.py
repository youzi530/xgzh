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


# ─── Sprint 11 BE-S11-D02: admin 管理 schemas ─────────────────


class KnowledgeArticleAdminDetail(KnowledgeArticleDetail):
    """admin 视角文章详情. 跟用户详情共用全部字段, 但能看到 is_published=False."""

    model_config = ConfigDict(from_attributes=True)


class KnowledgeArticleAdminListResponse(BaseModel):
    """admin 列表 (不过滤 is_published, 含全部文章). 列表轻量, 不带 content_md."""

    items: list[KnowledgeArticleSummary]
    total: int
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


class KnowledgeArticleCreate(BaseModel):
    """``POST /admin/knowledge/articles``.

    业务约束:
    - ``slug`` 全表唯一, 不可重复
    - ``content_md`` 必须传 (即便是占位 "# WIP")
    - ``toc_json`` 可空; admin 可选择手动维护或后续脚本自动生成
    """

    slug: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9-_]*$",
        description="URL-friendly key, e.g. 'hk-subscription-key-dates'",
    )
    title: str = Field(..., min_length=1, max_length=128)
    category: KnowledgeCategory = Field(..., description="hk / cn / general")
    tags: list[str] | None = Field(default=None)
    level: int = Field(default=1, ge=1, le=3, description="1=入门 / 2=进阶 / 3=实战")
    content_md: str = Field(..., min_length=1, max_length=200_000)
    toc_json: list[dict[str, Any]] | None = Field(default=None)
    is_published: bool = Field(default=False, description="新建默认草稿; 显式 true 才发布")
    source: KnowledgeSource = Field(default="curated")
    source_url: str | None = Field(default=None, max_length=2048)
    legal_disclaimer: str | None = Field(default=None, max_length=2000)


class KnowledgeArticleUpdate(BaseModel):
    """``PATCH /admin/knowledge/articles/{id}`` — 部分更新.

    所有字段都是 Optional. 没传 = 不动. slug 不可改 (改 slug 会破坏外链).
    """

    title: str | None = Field(default=None, min_length=1, max_length=128)
    category: KnowledgeCategory | None = None
    tags: list[str] | None = None
    level: int | None = Field(default=None, ge=1, le=3)
    content_md: str | None = Field(default=None, min_length=1, max_length=200_000)
    toc_json: list[dict[str, Any]] | None = None
    is_published: bool | None = None
    source: KnowledgeSource | None = None
    source_url: str | None = Field(default=None, max_length=2048)
    legal_disclaimer: str | None = Field(default=None, max_length=2000)


__all__ = [
    "KnowledgeArticleAdminDetail",
    "KnowledgeArticleAdminListResponse",
    "KnowledgeArticleCreate",
    "KnowledgeArticleDetail",
    "KnowledgeArticleSummary",
    "KnowledgeArticleUpdate",
    "KnowledgeCategoriesResponse",
    "KnowledgeCategory",
    "KnowledgeCategoryItem",
    "KnowledgeListResponse",
    "KnowledgeSource",
]
