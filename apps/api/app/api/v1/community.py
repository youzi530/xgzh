"""社区路由 (Sprint 6 BE-S6-006/007).

- 帖子: 发 / 列 / 详情 / 软删
- 评论: 发 / 列 / 软删
- 点赞: toggle (post / comment 通用)
- 举报: 提交

错误映射:
- :class:`PostNotFoundError` / :class:`CommentNotFoundError` → 404
- :class:`PostForbiddenError` / :class:`CommentForbiddenError` → 403
- :class:`NewUserReadOnlyError` → 403 (新用户 7d 只读)
- :class:`RateLimitExceeded` → 429 (main.py 全局)
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.db.models import User
from app.schemas.community import (
    CommentCreateRequest,
    CommentListResponse,
    CommentResponse,
    LikeRequest,
    LikeResponse,
    PostCategory,
    PostCreateRequest,
    PostDetailResponse,
    PostListResponse,
    ReportRequest,
    ReportResponse,
)
from app.security import get_current_user, get_optional_user
from app.services.community import interaction_service, post_service
from app.services.community.anti_spam import NewUserReadOnlyError
from app.services.community.interaction_service import (
    CommentForbiddenError,
    CommentNotFoundError,
)
from app.services.community.post_service import (
    PostForbiddenError,
    PostNotFoundError,
)

router = APIRouter(prefix="/community", tags=["community"])


# ─── 帖子 ──────────────────────────────────────────────────────────────


@router.post(
    "/posts",
    response_model=PostDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="发帖 (含 v3 内容审核 + 反 spam)",
    responses={
        403: {"description": "新用户 7d 只读 / 内容违规"},
        429: {"description": "提交过于频繁 (60s ≤ 1 / 24h ≤ 10)"},
    },
)
async def create_post_endpoint(
    req: PostCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PostDetailResponse:
    try:
        result = await post_service.create_post(
            session,
            user_id=user.user_id,
            content=req.content,
            category=req.category,
            related_ipo_code=req.related_ipo_code,
        )
    except NewUserReadOnlyError as e:
        raise HTTPException(
            status_code=403,
            detail="新用户 7 天内不能发帖, 请稍后再试",
        ) from e
    await session.commit()
    return _post_to_response(
        result.post,
        user_nickname=user.nickname,
        user_avatar_url=user.avatar_url,
        is_liked=False,
    )


@router.get(
    "/posts",
    response_model=PostListResponse,
    summary="帖子列表 / feed (默认 status=published 倒序)",
)
async def list_posts_endpoint(
    category: PostCategory | None = None,
    related_ipo_code: str | None = None,
    user_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
    user: User | None = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
) -> PostListResponse:
    requester_id = user.user_id if user else None
    data = await post_service.list_posts(
        session,
        requester_user_id=requester_id,
        category=category,
        related_ipo_code=related_ipo_code,
        user_id=user_id,
        page=page,
        page_size=page_size,
    )
    items = [
        _post_to_response(
            it["post"],
            user_nickname=it["user_nickname"],
            user_avatar_url=it["user_avatar_url"],
            is_liked=it["is_liked"],
        )
        for it in data["items"]
    ]
    return PostListResponse(
        items=items,
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
    )


@router.get(
    "/posts/{post_id}",
    response_model=PostDetailResponse,
    summary="帖子详情",
    responses={404: {"description": "帖子不存在或不可见"}},
)
async def get_post_endpoint(
    post_id: uuid.UUID,
    user: User | None = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
) -> PostDetailResponse:
    requester_id = user.user_id if user else None
    try:
        post, ctx = await post_service.get_post(
            session, post_id=post_id, requester_user_id=requester_id
        )
    except PostNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _post_to_response(
        post,
        user_nickname=ctx["user_nickname"],
        user_avatar_url=ctx["user_avatar_url"],
        is_liked=ctx["is_liked"],
    )


@router.delete(
    "/posts/{post_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="软删自己的帖子",
    responses={
        403: {"description": "非作者"},
        404: {"description": "帖子不存在"},
    },
)
async def delete_post_endpoint(
    post_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await post_service.delete_post(session, post_id=post_id, user_id=user.user_id)
    except PostNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PostForbiddenError as e:
        raise HTTPException(status_code=403, detail="无权删除该帖子") from e
    await session.commit()


# ─── 评论 ──────────────────────────────────────────────────────────────


@router.post(
    "/posts/{post_id}/comments",
    response_model=CommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="评论 (含 v3 内容审核 + 反 spam)",
    responses={
        403: {"description": "新用户 7d 只读 / 内容违规"},
        404: {"description": "帖子或父评论不存在"},
        429: {"description": "评论过于频繁 (10s ≤ 1 / 24h ≤ 50)"},
    },
)
async def create_comment_endpoint(
    post_id: uuid.UUID,
    req: CommentCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CommentResponse:
    try:
        comment = await interaction_service.create_comment(
            session,
            user_id=user.user_id,
            post_id=post_id,
            content=req.content,
            parent_comment_id=req.parent_comment_id,
        )
    except NewUserReadOnlyError as e:
        raise HTTPException(
            status_code=403, detail="新用户 7 天内不能评论"
        ) from e
    except CommentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except CommentForbiddenError as e:
        raise HTTPException(status_code=403, detail=f"内容违规: {e}") from e
    await session.commit()
    return CommentResponse(
        id=comment.id,
        post_id=comment.post_id,
        user_id=comment.user_id,
        user_nickname=user.nickname,
        user_avatar_url=user.avatar_url,
        parent_comment_id=comment.parent_comment_id,
        content=comment.content,
        status=comment.status,  # type: ignore[arg-type]
        likes_count=comment.likes_count,
        is_liked=False,
        created_at=comment.created_at,
    )


@router.get(
    "/posts/{post_id}/comments",
    response_model=CommentListResponse,
    summary="帖子评论列表 (parent_comment_id 控制层级)",
)
async def list_comments_endpoint(
    post_id: uuid.UUID,
    parent_comment_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 50,
    user: User | None = Depends(get_optional_user),
    session: AsyncSession = Depends(get_session),
) -> CommentListResponse:
    requester_id = user.user_id if user else None
    data = await interaction_service.list_comments(
        session,
        post_id=post_id,
        requester_user_id=requester_id,
        parent_comment_id=parent_comment_id,
        page=page,
        page_size=page_size,
    )
    items = [
        CommentResponse(
            id=it["comment"].id,
            post_id=it["comment"].post_id,
            user_id=it["comment"].user_id,
            user_nickname=it["user_nickname"],
            user_avatar_url=it["user_avatar_url"],
            parent_comment_id=it["comment"].parent_comment_id,
            content=it["comment"].content,
            status=it["comment"].status,
            likes_count=it["comment"].likes_count,
            is_liked=it["is_liked"],
            created_at=it["comment"].created_at,
        )
        for it in data["items"]
    ]
    return CommentListResponse(items=items, total=data["total"])


@router.delete(
    "/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="软删自己的评论",
)
async def delete_comment_endpoint(
    comment_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await interaction_service.delete_comment(
            session, user_id=user.user_id, comment_id=comment_id
        )
    except CommentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except CommentForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    await session.commit()


# ─── 点赞 ──────────────────────────────────────────────────────────────


@router.post(
    "/likes",
    response_model=LikeResponse,
    summary="切换点赞 (post / comment 通用; 幂等)",
    responses={
        404: {"description": "目标不存在或已删除"},
        429: {"description": "操作过于频繁 (1s ≤ 5)"},
    },
)
async def toggle_like_endpoint(
    req: LikeRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> LikeResponse:
    try:
        liked, count = await interaction_service.toggle_like(
            session,
            user_id=user.user_id,
            target_type=req.target_type,
            target_id=req.target_id,
        )
    except CommentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    await session.commit()
    return LikeResponse(
        target_type=req.target_type,
        target_id=req.target_id,
        liked=liked,
        likes_count=count,
    )


# ─── 举报 ──────────────────────────────────────────────────────────────


@router.post(
    "/reports",
    response_model=ReportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="举报帖子 / 评论 (60s ≤ 1 / 24h ≤ 5)",
    responses={
        404: {"description": "目标不存在"},
        429: {"description": "举报过于频繁"},
    },
)
async def create_report_endpoint(
    req: ReportRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReportResponse:
    try:
        report = await interaction_service.create_report(
            session,
            reporter_user_id=user.user_id,
            target_type=req.target_type,
            target_id=req.target_id,
            reason=req.reason,
            detail=req.detail,
        )
    except CommentNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    await session.commit()
    return ReportResponse(
        id=report.id,
        target_type=req.target_type,
        target_id=req.target_id,
        reason=req.reason,
        status=report.status,  # type: ignore[arg-type]
        created_at=report.created_at,
    )


# ─── 工具 ──────────────────────────────────────────────────────────────


def _post_to_response(
    post: Any,
    *,
    user_nickname: str | None,
    user_avatar_url: str | None,
    is_liked: bool,
) -> PostDetailResponse:
    return PostDetailResponse(
        id=post.id,
        user_id=post.user_id,
        user_nickname=user_nickname,
        user_avatar_url=user_avatar_url,
        content=post.content,
        status=post.status,
        visibility=post.visibility,
        category=post.category,
        related_ipo_code=post.related_ipo_code,
        likes_count=post.likes_count,
        comments_count=post.comments_count,
        reports_count=post.reports_count,
        rejection_reason=post.rejection_reason,
        is_liked=is_liked,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )
