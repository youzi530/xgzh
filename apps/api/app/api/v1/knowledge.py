"""知识库路由 (Sprint 6 BE-S6-004).

公开 (匿名 + 登录都可读), 无登录鉴权 — 知识库是普及内容, 越多人看越好.

- ``GET /api/v1/knowledge``               列表 (page + filter)
- ``GET /api/v1/knowledge/categories``    分类 + 计数
- ``GET /api/v1/knowledge/{slug}``        详情 (异步 view_count++)

路径顺序: ``/categories`` 必须在 ``/{slug}`` 之前注册, 否则会被 slug 路由吃掉.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas.knowledge import (
    KnowledgeArticleDetail,
    KnowledgeArticleSummary,
    KnowledgeCategoriesResponse,
    KnowledgeCategory,
    KnowledgeCategoryItem,
    KnowledgeListResponse,
)
from app.services import knowledge_service
from app.services.knowledge_service import KnowledgeNotFoundError

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get(
    "",
    response_model=KnowledgeListResponse,
    summary="列已发布知识文章 (page + filter)",
)
async def list_knowledge(
    session: AsyncSession = Depends(get_session),
    category: KnowledgeCategory | None = None,
    level: int | None = None,
    tag: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> KnowledgeListResponse:
    rows, total = await knowledge_service.list_articles(
        session,
        category=category,
        level=level,
        tag=tag,
        page=page,
        page_size=page_size,
    )
    return KnowledgeListResponse(
        items=[KnowledgeArticleSummary.model_validate(r) for r in rows],
        total=total,
        page=max(page, 1),
        page_size=min(max(page_size, 1), 100),
    )


@router.get(
    "/categories",
    response_model=KnowledgeCategoriesResponse,
    summary="知识分类 + 各类计数",
)
async def list_categories(
    session: AsyncSession = Depends(get_session),
) -> KnowledgeCategoriesResponse:
    items, grand = await knowledge_service.get_categories(session)
    return KnowledgeCategoriesResponse(
        items=[
            KnowledgeCategoryItem(
                category=cat,  # type: ignore[arg-type]
                label=label,
                count=count,
            )
            for (cat, label, count) in items
        ],
        total=grand,
    )


@router.get(
    "/{slug}",
    response_model=KnowledgeArticleDetail,
    summary="知识文章详情 (异步 view_count++)",
    responses={404: {"description": "文章不存在或未发布"}},
)
async def get_knowledge(
    slug: str,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> KnowledgeArticleDetail:
    try:
        row = await knowledge_service.get_article_by_slug(session, slug=slug)
    except KnowledgeNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found") from e
    # 异步 view_count++; 不阻塞响应; 失败 logger.warning
    background.add_task(knowledge_service.bump_view_count, row.id)
    return KnowledgeArticleDetail.model_validate(row)
