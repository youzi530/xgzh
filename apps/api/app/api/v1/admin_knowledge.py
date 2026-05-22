"""Admin 知识库管理路由 (Sprint 11 BE-S11-D03).

6 个 endpoint:

| Method | Path                                          | 用途                |
|--------|-----------------------------------------------|---------------------|
| GET    | /api/v1/admin/knowledge/articles              | 列表 + filter + 分页 |
| GET    | /api/v1/admin/knowledge/articles/{id}         | 单篇详情 (含草稿)    |
| POST   | /api/v1/admin/knowledge/articles              | 新建文章            |
| PATCH  | /api/v1/admin/knowledge/articles/{id}         | 部分更新            |
| DELETE | /api/v1/admin/knowledge/articles/{id}         | 硬删                |

不暴露 ``slug`` 修改 (改 slug 破坏外链 / SEO; 错了就重建).

鉴权: ``get_current_admin`` JWT + ``is_admin=true``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import User
from app.schemas.knowledge import (
    KnowledgeArticleAdminDetail,
    KnowledgeArticleAdminListResponse,
    KnowledgeArticleCreate,
    KnowledgeArticleSummary,
    KnowledgeArticleUpdate,
    KnowledgeCategory,
)
from app.security.deps import get_current_admin
from app.services import knowledge_service
from app.services.admin_audit_service import (
    log_admin_action,
    resolve_request_context,
)
from app.services.knowledge_service import (
    KnowledgeNotFoundError,
    KnowledgeSlugTakenError,
)

router = APIRouter(prefix="/admin/knowledge", tags=["admin"])


def _not_found(article_id: uuid.UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "article_not_found",
            "message": f"article {article_id} 不存在",
        },
    )


# ─── 1. GET /admin/knowledge/articles ─────────────────────────


@router.get(
    "/articles",
    response_model=KnowledgeArticleAdminListResponse,
    status_code=status.HTTP_200_OK,
    summary="管理员: 知识库文章列表 (含未发布; filter + 分页)",
    responses={
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
    },
)
async def list_articles_admin(
    q: str | None = Query(default=None, max_length=200, description="title 模糊搜"),
    category: KnowledgeCategory | None = Query(default=None),
    level: int | None = Query(default=None, ge=1, le=3),
    is_published: bool | None = Query(
        default=None,
        description="None=全部 (含草稿); true=只看已发布; false=只看草稿",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeArticleAdminListResponse:
    items, total = await knowledge_service.admin_list_articles(
        session,
        q=q,
        category=category,
        level=level,
        is_published=is_published,
        page=page,
        page_size=page_size,
    )
    logger.info(
        f"admin.knowledge.list admin_id={admin.user_id} q={q!r} category={category} "
        f"is_published={is_published} returned={len(items)}/{total}"
    )
    return KnowledgeArticleAdminListResponse(
        items=[KnowledgeArticleSummary.model_validate(a) for a in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ─── 2. GET /admin/knowledge/articles/{id} ────────────────────


@router.get(
    "/articles/{article_id}",
    response_model=KnowledgeArticleAdminDetail,
    status_code=status.HTTP_200_OK,
    summary="管理员: 文章详情 (含未发布)",
    responses={
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "id 不存在"},
    },
)
async def get_article_admin(
    article_id: uuid.UUID,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeArticleAdminDetail:
    try:
        article = await knowledge_service.admin_get_article(
            session, article_id=article_id
        )
    except KnowledgeNotFoundError as e:
        raise _not_found(article_id) from e
    logger.info(f"admin.knowledge.detail admin_id={admin.user_id} id={article_id}")
    return KnowledgeArticleAdminDetail.model_validate(article)


# ─── 3. POST /admin/knowledge/articles ────────────────────────


@router.post(
    "/articles",
    response_model=KnowledgeArticleAdminDetail,
    status_code=status.HTTP_201_CREATED,
    summary="管理员: 新建文章 (默认草稿)",
    responses={
        201: {"description": "创建成功"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        409: {"description": "slug 已被占用"},
        422: {"description": "字段非法"},
    },
)
async def create_article_admin(
    body: KnowledgeArticleCreate,
    request: Request,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeArticleAdminDetail:
    ip, ua = resolve_request_context(request)
    try:
        article = await knowledge_service.create_article(
            session,
            slug=body.slug,
            title=body.title,
            category=body.category,
            content_md=body.content_md,
            tags=body.tags,
            level=body.level,
            toc_json=body.toc_json,
            is_published=body.is_published,
            source=body.source,
            source_url=body.source_url,
            legal_disclaimer=body.legal_disclaimer,
        )
    except KnowledgeSlugTakenError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "slug_taken", "message": str(e)},
        ) from e
    await log_admin_action(
        admin_user_id=admin.user_id,
        action="create",
        target_type="knowledge_article",
        target_id=str(article.id),
        changes={
            "slug": [None, article.slug],
            "title": [None, article.title],
            "is_published": [None, article.is_published],
        },
        ip_inet=ip,
        user_agent=ua,
    )
    logger.warning(
        f"admin.knowledge.create admin_id={admin.user_id} id={article.id} "
        f"slug={article.slug}"
    )
    return KnowledgeArticleAdminDetail.model_validate(article)


# ─── 4. PATCH /admin/knowledge/articles/{id} ──────────────────


@router.patch(
    "/articles/{article_id}",
    response_model=KnowledgeArticleAdminDetail,
    status_code=status.HTTP_200_OK,
    summary="管理员: 部分更新文章 (slug 不可改)",
    responses={
        200: {"description": "更新成功"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "id 不存在"},
        422: {"description": "字段非法"},
    },
)
async def update_article_admin(
    article_id: uuid.UUID,
    body: KnowledgeArticleUpdate,
    request: Request,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeArticleAdminDetail:
    ip, ua = resolve_request_context(request)
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        try:
            article = await knowledge_service.admin_get_article(
                session, article_id=article_id
            )
        except KnowledgeNotFoundError as e:
            raise _not_found(article_id) from e
        return KnowledgeArticleAdminDetail.model_validate(article)
    try:
        article = await knowledge_service.update_article(
            session, article_id=article_id, patch=patch
        )
    except KnowledgeNotFoundError as e:
        raise _not_found(article_id) from e
    await log_admin_action(
        admin_user_id=admin.user_id,
        action="update",
        target_type="knowledge_article",
        target_id=str(article_id),
        changes={k: [None, v] for k, v in patch.items()},
        ip_inet=ip,
        user_agent=ua,
    )
    logger.warning(
        f"admin.knowledge.update admin_id={admin.user_id} id={article_id} "
        f"fields={list(patch.keys())}"
    )
    return KnowledgeArticleAdminDetail.model_validate(article)


# ─── 5. DELETE /admin/knowledge/articles/{id} ─────────────────


@router.delete(
    "/articles/{article_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="管理员: 硬删文章",
    responses={
        204: {"description": "删除成功"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "id 不存在"},
    },
)
async def delete_article_admin(
    article_id: uuid.UUID,
    request: Request,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    ip, ua = resolve_request_context(request)
    try:
        await knowledge_service.delete_article(session, article_id=article_id)
    except KnowledgeNotFoundError as e:
        raise _not_found(article_id) from e
    await log_admin_action(
        admin_user_id=admin.user_id,
        action="delete",
        target_type="knowledge_article",
        target_id=str(article_id),
        ip_inet=ip,
        user_agent=ua,
    )
    logger.warning(
        f"admin.knowledge.delete admin_id={admin.user_id} id={article_id}"
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
