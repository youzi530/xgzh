"""社区评论 / 点赞 / 举报业务服务 (Sprint 6 BE-S6-007).

接口
====
评论:
- :func:`create_comment`     发评论 (走 audit + anti-spam)
- :func:`list_comments`      帖子下评论列表 (一级评论, 二级折叠)
- :func:`delete_comment`     软删自己的评论
点赞:
- :func:`toggle_like`        幂等切换 (post / comment 通用)
举报:
- :func:`create_report`      举报 (60s 1 次 / 24h 5 次)
- :func:`list_reports`       admin 队列 (待办)

设计要点
========
1. **评论审核同 post**: ``audit_user_content`` (v3) 走一遍, 决定 status.
2. **点赞幂等**: UNIQUE(user_id, target_type, target_id) DB 强约束 + 应用层
   先查再增. ``toggle_like`` 自动判断当前 liked 状态, 已赞→取消, 未赞→新增.
3. **likes_count / comments_count 同步累加**: 点赞 / 取消都更新对应主表;
   评论计数仅在 ``status='published'`` 时累加 (避免 pending 评论灌水显示).
4. **举报阈值**: ``reports_count >= 5`` 自动隐藏帖子 (status='hidden') 等 admin 审,
   不立即影响 likes_count. admin 处理时手动恢复 / 永久删除.
"""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger
from sqlalchemy import and_, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CommunityComment,
    CommunityLike,
    CommunityPost,
    CommunityReport,
    User,
)
from app.services.community.anti_spam import (
    enforce_comment_rate,
    enforce_like_rate,
    enforce_new_user_writable,
    enforce_report_rate,
)
from app.services.community.audit import audit_user_content

_REPORTS_AUTO_HIDE_THRESHOLD = 5


class CommentNotFoundError(Exception):
    """评论不存在或软删 — router 转 404."""


class CommentForbiddenError(Exception):
    """非作者操作他人评论 — router 转 403."""


# ─── 评论 ──────────────────────────────────────────────────────────────


async def _verify_post_exists_published(
    session: AsyncSession, post_id: uuid.UUID
) -> CommunityPost:
    """评论 / 点赞 / 举报前先校验 post 存在且非 deleted."""
    stmt = select(CommunityPost).where(CommunityPost.id == post_id)
    post = (await session.execute(stmt)).scalar_one_or_none()
    if post is None or post.status == "deleted":
        raise CommentNotFoundError(f"post not found: {post_id}")
    return post


async def create_comment(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    post_id: uuid.UUID,
    content: str,
    parent_comment_id: uuid.UUID | None = None,
) -> CommunityComment:
    """发评论. 二级评论 ``parent_comment_id`` 必须指向同帖一级评论."""
    await enforce_new_user_writable(session, user_id=user_id)
    await enforce_comment_rate(user_id=user_id)
    post = await _verify_post_exists_published(session, post_id)

    if parent_comment_id is not None:
        # 二级评论: parent 必须存在 + 同 post + 自身不是二级
        parent_stmt = select(CommunityComment).where(
            CommunityComment.id == parent_comment_id
        )
        parent = (await session.execute(parent_stmt)).scalar_one_or_none()
        if (
            parent is None
            or parent.post_id != post_id
            or parent.parent_comment_id is not None
            or parent.status != "published"
        ):
            raise CommentNotFoundError(
                f"parent comment invalid: {parent_comment_id}"
            )

    audit = audit_user_content(content, user_id=str(user_id))
    if audit.verdict == "reject":
        # 评论 reject 直接 raise — 与 post 不同, 评论没有 self_only 视图
        logger.info(
            f"community.comment.reject user={user_id} post={post_id} "
            f"reason={audit.rejection_reason}"
        )
        raise CommentForbiddenError(audit.rejection_reason or "content_violation")

    status_val = "published" if audit.verdict == "approve" else "pending"
    comment = CommunityComment(
        post_id=post_id,
        user_id=user_id,
        parent_comment_id=parent_comment_id,
        content=content,
        status=status_val,
    )
    session.add(comment)
    await session.flush()

    # 仅 published 时累加 comments_count
    if status_val == "published":
        await session.execute(
            update(CommunityPost)
            .where(CommunityPost.id == post_id)
            .values(comments_count=CommunityPost.comments_count + 1)
        )

    await session.refresh(comment)
    logger.info(
        f"community.comment.create user={user_id} post={post_id} "
        f"comment={comment.id} status={status_val}"
    )
    # 避免未使用 import warning (post 用作存在校验)
    _ = post
    return comment


async def list_comments(
    session: AsyncSession,
    *,
    post_id: uuid.UUID,
    requester_user_id: uuid.UUID | None,
    parent_comment_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """列评论. ``parent_comment_id=None`` 列一级, 传值列其下二级."""
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    offset = (page - 1) * page_size

    base_filters = [
        CommunityComment.post_id == post_id,
        CommunityComment.status == "published",
    ]
    if parent_comment_id is None:
        base_filters.append(CommunityComment.parent_comment_id.is_(None))
    else:
        base_filters.append(CommunityComment.parent_comment_id == parent_comment_id)

    count_stmt = select(func.count(CommunityComment.id)).where(and_(*base_filters))
    total = int((await session.execute(count_stmt)).scalar() or 0)

    stmt = (
        select(CommunityComment, User.nickname, User.avatar_url)
        .join(User, User.user_id == CommunityComment.user_id)
        .where(and_(*base_filters))
        .order_by(CommunityComment.created_at.asc())
        .limit(page_size)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).all()

    liked_ids: set[uuid.UUID] = set()
    if requester_user_id and rows:
        comment_ids = [r[0].id for r in rows]
        like_stmt = select(CommunityLike.target_id).where(
            and_(
                CommunityLike.user_id == requester_user_id,
                CommunityLike.target_type == "comment",
                CommunityLike.target_id.in_(comment_ids),
            )
        )
        liked_ids = set((await session.execute(like_stmt)).scalars().all())

    items = [
        {
            "comment": c,
            "user_nickname": nick,
            "user_avatar_url": avatar,
            "is_liked": c.id in liked_ids,
        }
        for c, nick, avatar in rows
    ]
    return {"items": items, "total": total}


async def delete_comment(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    comment_id: uuid.UUID,
) -> None:
    stmt = select(CommunityComment).where(CommunityComment.id == comment_id)
    comment = (await session.execute(stmt)).scalar_one_or_none()
    if comment is None or comment.status == "deleted":
        raise CommentNotFoundError(f"comment not found: {comment_id}")
    if comment.user_id != user_id:
        raise CommentForbiddenError("not owner")
    was_published = comment.status == "published"
    comment.status = "deleted"
    comment.content = "[已删除]"
    if was_published:
        await session.execute(
            update(CommunityPost)
            .where(CommunityPost.id == comment.post_id)
            .values(comments_count=func.greatest(CommunityPost.comments_count - 1, 0))
        )
    await session.flush()
    logger.info(f"community.comment.delete user={user_id} comment={comment_id}")


# ─── 点赞 ──────────────────────────────────────────────────────────────


async def toggle_like(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
) -> tuple[bool, int]:
    """幂等切换点赞.

    Returns:
        (liked, likes_count): 操作后状态. liked=True 表示当前已赞.
    """
    if target_type not in ("post", "comment"):
        raise ValueError(f"invalid target_type: {target_type}")

    # 校验目标存在且非 deleted
    if target_type == "post":
        target_post = await _verify_post_exists_published(session, target_id)
        target_status = target_post.status
    else:
        cstmt = select(CommunityComment).where(CommunityComment.id == target_id)
        c = (await session.execute(cstmt)).scalar_one_or_none()
        if c is None or c.status == "deleted":
            raise CommentNotFoundError(f"target not found: {target_id}")
        target_status = c.status
    if target_status not in ("published", "pending", "hidden"):
        # rejected / deleted 不能赞
        raise CommentNotFoundError(f"target not likable: {target_id}")

    await enforce_like_rate(user_id=user_id)

    # 查现有点赞
    existing_stmt = select(CommunityLike).where(
        and_(
            CommunityLike.user_id == user_id,
            CommunityLike.target_type == target_type,
            CommunityLike.target_id == target_id,
        )
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()

    if existing is not None:
        # 取消点赞
        await session.delete(existing)
        liked = False
        delta = -1
    else:
        # 新增点赞
        like = CommunityLike(
            user_id=user_id,
            target_type=target_type,
            target_id=target_id,
        )
        session.add(like)
        liked = True
        delta = 1

    # 累加计数
    if target_type == "post":
        await session.execute(
            update(CommunityPost)
            .where(CommunityPost.id == target_id)
            .values(likes_count=func.greatest(CommunityPost.likes_count + delta, 0))
        )
        await session.flush()
        new_count = (
            await session.execute(
                select(CommunityPost.likes_count).where(
                    CommunityPost.id == target_id
                )
            )
        ).scalar_one()
    else:
        await session.execute(
            update(CommunityComment)
            .where(CommunityComment.id == target_id)
            .values(likes_count=func.greatest(CommunityComment.likes_count + delta, 0))
        )
        await session.flush()
        new_count = (
            await session.execute(
                select(CommunityComment.likes_count).where(
                    CommunityComment.id == target_id
                )
            )
        ).scalar_one()

    logger.info(
        f"community.like.toggle user={user_id} type={target_type} "
        f"target={target_id} liked={liked}"
    )
    return liked, int(new_count)


async def get_like_status(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
) -> bool:
    """单次查询是否已赞 (FE 偶尔单点查; 主流量在 list/detail 内部 batch)."""
    stmt = select(
        exists().where(
            and_(
                CommunityLike.user_id == user_id,
                CommunityLike.target_type == target_type,
                CommunityLike.target_id == target_id,
            )
        )
    )
    return bool((await session.execute(stmt)).scalar())


# ─── 举报 ──────────────────────────────────────────────────────────────


async def create_report(
    session: AsyncSession,
    *,
    reporter_user_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    reason: str,
    detail: str | None = None,
) -> CommunityReport:
    """举报. 同帖累计 ≥ 5 自动隐藏 (status='hidden') 等 admin 审."""
    if target_type not in ("post", "comment"):
        raise ValueError(f"invalid target_type: {target_type}")

    await enforce_report_rate(user_id=reporter_user_id)

    # 校验目标存在
    if target_type == "post":
        await _verify_post_exists_published(session, target_id)
    else:
        cstmt = select(CommunityComment).where(CommunityComment.id == target_id)
        c = (await session.execute(cstmt)).scalar_one_or_none()
        if c is None or c.status == "deleted":
            raise CommentNotFoundError(f"target not found: {target_id}")

    report = CommunityReport(
        reporter_user_id=reporter_user_id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        detail=detail,
    )
    session.add(report)
    await session.flush()

    # 累加 reports_count + 触发自动隐藏
    if target_type == "post":
        await session.execute(
            update(CommunityPost)
            .where(CommunityPost.id == target_id)
            .values(reports_count=CommunityPost.reports_count + 1)
        )
        # 检查是否触阈值
        new_count_stmt = select(CommunityPost.reports_count, CommunityPost.status).where(
            CommunityPost.id == target_id
        )
        row = (await session.execute(new_count_stmt)).one()
        if row[0] >= _REPORTS_AUTO_HIDE_THRESHOLD and row[1] == "published":
            await session.execute(
                update(CommunityPost)
                .where(CommunityPost.id == target_id)
                .values(status="hidden")
            )
            logger.warning(
                f"community.post.auto_hide post={target_id} reports={row[0]}"
            )

    await session.refresh(report)
    logger.info(
        f"community.report.create user={reporter_user_id} type={target_type} "
        f"target={target_id} reason={reason}"
    )
    return report


async def list_reports_admin(
    session: AsyncSession,
    *,
    status_filter: str = "pending",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """admin 队列: 列待审/已处理举报.

    **不做 admin role 检查** — router 层走 ``require_admin_token`` 装饰器.
    """
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    offset = (page - 1) * page_size

    count_stmt = select(func.count(CommunityReport.id)).where(
        CommunityReport.status == status_filter
    )
    total = int((await session.execute(count_stmt)).scalar() or 0)

    stmt = (
        select(CommunityReport)
        .where(CommunityReport.status == status_filter)
        .order_by(CommunityReport.created_at.desc())
        .limit(page_size)
        .offset(offset)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return {"items": rows, "total": total, "page": page, "page_size": page_size}
