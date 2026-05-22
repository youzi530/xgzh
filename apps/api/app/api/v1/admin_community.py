"""Admin 社区管理路由 (Sprint 11 BE-S11-C02).

5 个 endpoint, 全部走 ``get_current_admin`` JWT + ``is_admin=true`` 鉴权:

| Method | Path                                              | 用途                            |
|--------|---------------------------------------------------|---------------------------------|
| GET    | /api/v1/admin/community/posts                     | 列表 + filter (status/visibility/q/has_reports) |
| GET    | /api/v1/admin/community/posts/{post_id}           | 单帖详情 (含所有 status, 含 deleted) |
| PATCH  | /api/v1/admin/community/posts/{post_id}/status    | 强制改 status (published/pending/rejected/deleted/hidden) |
| PATCH  | /api/v1/admin/community/posts/{post_id}/visibility | 强制改 visibility (public/self_only); 隐藏帖子 |
| DELETE | /api/v1/admin/community/posts/{post_id}           | = PATCH status=deleted, 但语义更明确 |

拍板 Q2=B (简化版):
- ❌ 不实现完整审核队列 (spec/13 4 选项 approve/reject/hidden_continue/delete)
- ❌ 不改用户原文 content (PIPL 防篡改)
- ❌ 不改 user_id / category / related_ipo_code (审计字段)
- ✅ 改 status (强制 published → pending 等)
- ✅ 改 visibility (软隐藏, status 仍 published)
- ✅ 软删 (= PATCH status=deleted)

后续 Sprint 12 加: 完整审核队列 + 24h SLA + 钉钉告警.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import User
from app.schemas.community import (
    AdminPostListItem,
    AdminPostListResponse,
    AdminPostStatusUpdate,
    AdminPostVisibilityUpdate,
    PostCategory,
    PostStatus,
    PostVisibility,
)
from app.security.deps import get_current_admin
from app.services.community import post_service
from app.services.community.post_service import PostNotFoundError

router = APIRouter(prefix="/admin/community", tags=["admin"])


def _to_item(post, nickname, avatar_url) -> AdminPostListItem:  # type: ignore[no-untyped-def]
    return AdminPostListItem.model_validate(
        {
            "id": post.id,
            "user_id": post.user_id,
            "user_nickname": nickname,
            "user_avatar_url": avatar_url,
            "content": post.content,
            "status": post.status,
            "visibility": post.visibility,
            "category": post.category,
            "related_ipo_code": post.related_ipo_code,
            "likes_count": post.likes_count,
            "comments_count": post.comments_count,
            "reports_count": post.reports_count,
            "rejection_reason": post.rejection_reason,
            "reviewed_by": post.reviewed_by,
            "reviewed_at": post.reviewed_at,
            "created_at": post.created_at,
            "updated_at": post.updated_at,
        }
    )


def _not_found(post_id: uuid.UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "post_not_found",
            "message": f"post {post_id} 不存在",
        },
    )


# ─── 1. GET /admin/community/posts — 列表 ──────────────────────


@router.get(
    "/posts",
    response_model=AdminPostListResponse,
    status_code=status.HTTP_200_OK,
    summary="管理员: 帖子列表 (含所有 status; 多维 filter + 分页)",
    responses={
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
    },
)
async def list_posts_admin(
    q: str | None = Query(default=None, max_length=200, description="content 模糊搜"),
    status_filter: PostStatus | None = Query(
        default=None,
        alias="status",
        description="filter by status: pending/published/rejected/deleted/hidden",
    ),
    visibility: PostVisibility | None = Query(default=None),
    category: PostCategory | None = Query(default=None),
    has_reports: bool | None = Query(
        default=None,
        description="True = 只看被举报的帖子 (admin 优先处理); 不传 = 全返",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminPostListResponse:
    result = await post_service.admin_list_posts(
        session,
        q=q,
        status_filter=status_filter,
        visibility=visibility,
        category=category,
        has_reports=has_reports,
        page=page,
        page_size=page_size,
    )
    logger.info(
        f"admin.community.list admin_id={admin.user_id} q={q!r} status={status_filter} "
        f"visibility={visibility} category={category} has_reports={has_reports} "
        f"page={page} returned={len(result['items'])}/{result['total']}"
    )
    return AdminPostListResponse(
        items=[
            _to_item(i["post"], i["user_nickname"], i["user_avatar_url"])
            for i in result["items"]
        ],
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


# ─── 2. GET /admin/community/posts/{id} — 详情 ─────────────────


@router.get(
    "/posts/{post_id}",
    response_model=AdminPostListItem,
    status_code=status.HTTP_200_OK,
    summary="管理员: 单帖详情 (含 deleted 状态)",
    responses={
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "post_id 不存在"},
    },
)
async def get_post_admin(
    post_id: uuid.UUID,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminPostListItem:
    try:
        post, ctx = await post_service.admin_get_post(session, post_id=post_id)
    except PostNotFoundError as e:
        raise _not_found(post_id) from e
    logger.info(f"admin.community.detail admin_id={admin.user_id} post={post_id}")
    return _to_item(post, ctx["user_nickname"], ctx["user_avatar_url"])


# ─── 3. PATCH /admin/community/posts/{id}/status ──────────────


@router.patch(
    "/posts/{post_id}/status",
    response_model=AdminPostListItem,
    status_code=status.HTTP_200_OK,
    summary="管理员: 强制改 post status",
    responses={
        200: {"description": "更新成功"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "post_id 不存在"},
        422: {"description": "status 非法"},
    },
)
async def update_post_status_admin(
    post_id: uuid.UUID,
    body: AdminPostStatusUpdate,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminPostListItem:
    try:
        post = await post_service.admin_update_post_status(
            session,
            admin=admin,
            post_id=post_id,
            new_status=body.status,
            reason=body.reason,
        )
    except PostNotFoundError as e:
        raise _not_found(post_id) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_status", "message": str(e)},
        ) from e
    _, ctx = await post_service.admin_get_post(session, post_id=post_id)
    return _to_item(post, ctx["user_nickname"], ctx["user_avatar_url"])


# ─── 4. PATCH /admin/community/posts/{id}/visibility ─────────


@router.patch(
    "/posts/{post_id}/visibility",
    response_model=AdminPostListItem,
    status_code=status.HTTP_200_OK,
    summary="管理员: 强制改 post visibility (软隐藏)",
    responses={
        200: {"description": "更新成功"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "post_id 不存在"},
    },
)
async def update_post_visibility_admin(
    post_id: uuid.UUID,
    body: AdminPostVisibilityUpdate,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminPostListItem:
    try:
        post = await post_service.admin_update_post_visibility(
            session,
            admin=admin,
            post_id=post_id,
            new_visibility=body.visibility,
        )
    except PostNotFoundError as e:
        raise _not_found(post_id) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_visibility", "message": str(e)},
        ) from e
    _, ctx = await post_service.admin_get_post(session, post_id=post_id)
    return _to_item(post, ctx["user_nickname"], ctx["user_avatar_url"])


# ─── 5. DELETE /admin/community/posts/{id} ────────────────────


@router.delete(
    "/posts/{post_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="管理员: 强删帖子 (= status=deleted; 幂等)",
    responses={
        204: {"description": "删除成功 (已删的也 204)"},
        401: {"description": "未登录 / token 无效"},
        403: {"description": "已登录但非管理员"},
        404: {"description": "post_id 不存在"},
    },
)
async def delete_post_admin(
    post_id: uuid.UUID,
    admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        await post_service.admin_delete_post(session, admin=admin, post_id=post_id)
    except PostNotFoundError as e:
        raise _not_found(post_id) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
