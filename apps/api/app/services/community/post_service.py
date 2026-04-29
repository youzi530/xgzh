"""社区发帖业务服务 (Sprint 6 BE-S6-006).

接口
====
- :func:`create_post`     发帖 (走 audit + anti-spam)
- :func:`list_posts`      feed 列表 (status=published 倒序)
- :func:`get_post`        详情 (含 is_liked join, 软删 / hidden 视为 404 给非作者)
- :func:`delete_post`     软删 (用户主动 = status=deleted)

设计要点
========

1. **发帖审核 + 限流串行**:
   - audit (v3) 先跑 → 决定 status 终态 (approve / reject / queue)
   - anti-spam 先 enforce (可能 429)
   - status 变更后入库; 反 spam 限流的目的是防黑产, 内容审核是防违法
2. **软删而非 DROP**: ``status=deleted`` 让举报记录 / 评论历史还能 trace
3. **feed 列表 N+1 优化**: 单 SQL 一次 JOIN users + 子查询 likes (subquery EXISTS)
4. **跨用户访问软删帖**: status in (deleted/hidden/rejected) → 404; status=pending
   作者自己能看, 别人 404
5. **logger 落 audit / spam / 软删** 关键事件 — 上线后可拼成"风控 dashboard"
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    CommunityLike,
    CommunityPost,
    User,
)
from app.services.community.anti_spam import enforce_new_user_writable, enforce_post_rate
from app.services.community.audit import AuditResult, audit_user_content


class PostNotFoundError(Exception):
    """帖子不存在 / 软删 / 不属于该用户 — router 转 404."""


class PostForbiddenError(Exception):
    """非作者操作他人帖 — router 转 403."""


@dataclass(frozen=True, slots=True)
class CreatePostResult:
    """发帖结果. ``post`` 是入库后的 ORM, ``audit`` 是审核报告 (用于响应里告知用户).

    ``audit.verdict``:
    - approve → post.status='published'
    - queue → post.status='pending', 进 admin 队列
    - reject → post.status='rejected', visibility='self_only', UI 显拒绝原因
    """

    post: CommunityPost
    audit: AuditResult


# ─── 发帖 ──────────────────────────────────────────────────────────────


async def create_post(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    content: str,
    category: str = "general",
    related_ipo_code: str | None = None,
) -> CreatePostResult:
    """发帖主逻辑.

    顺序:
    1. enforce_new_user_writable (新用户 7d 只读)
    2. enforce_post_rate (60s ≤ 1 / 24h ≤ 10)
    3. audit_user_content (Tier1 reject / Tier2 queue / approve)
    4. 入库 (status 由 verdict 决定)
    """
    await enforce_new_user_writable(session, user_id=user_id)
    await enforce_post_rate(user_id=user_id)
    audit = audit_user_content(content, user_id=str(user_id))

    if audit.verdict == "approve":
        status = "published"
        visibility = "public"
        rejection_reason = None
    elif audit.verdict == "queue":
        status = "pending"
        visibility = "public"
        rejection_reason = None
    else:  # reject
        status = "rejected"
        visibility = "self_only"
        rejection_reason = audit.rejection_reason or "content_violation"

    post = CommunityPost(
        user_id=user_id,
        content=content,
        status=status,
        visibility=visibility,
        category=category,
        related_ipo_code=related_ipo_code,
        rejection_reason=rejection_reason,
    )
    session.add(post)
    await session.flush()
    await session.refresh(post)

    logger.info(
        f"community.post.create user={user_id} post={post.id} "
        f"verdict={audit.verdict} status={status}"
    )
    return CreatePostResult(post=post, audit=audit)


# ─── 详情 / 列表 ───────────────────────────────────────────────────────


def _is_post_visible_to(post: CommunityPost, *, user_id: uuid.UUID) -> bool:
    """判断帖子对 ``user_id`` 是否可见.

    - status='published' → 所有人可见
    - status='pending' / 'rejected' / 'hidden' → 仅作者
    - status='deleted' → 谁都不可见
    """
    if post.status == "deleted":
        return False
    if post.status == "published":
        return True
    return post.user_id == user_id


async def get_post(
    session: AsyncSession,
    *,
    post_id: uuid.UUID,
    requester_user_id: uuid.UUID | None,
) -> tuple[CommunityPost, dict[str, Any]]:
    """取详情 + user 冗余 + is_liked.

    Returns:
        (post, ctx) — ctx 含 user_nickname / user_avatar_url / is_liked
    """
    stmt = select(CommunityPost).where(CommunityPost.id == post_id)
    res = await session.execute(stmt)
    post = res.scalar_one_or_none()
    if post is None:
        raise PostNotFoundError(f"post not found: {post_id}")
    if not _is_post_visible_to(post, user_id=requester_user_id or uuid.UUID(int=0)):
        # 不区分 "deleted" / "self_only", 一律 404 防探测
        raise PostNotFoundError(f"post not visible: {post_id}")

    user_stmt = select(User.nickname, User.avatar_url).where(User.user_id == post.user_id)
    u = (await session.execute(user_stmt)).one_or_none()
    user_nickname = u[0] if u else None
    user_avatar_url = u[1] if u else None

    is_liked = False
    if requester_user_id:
        like_stmt = select(
            exists().where(
                and_(
                    CommunityLike.user_id == requester_user_id,
                    CommunityLike.target_type == "post",
                    CommunityLike.target_id == post_id,
                )
            )
        )
        is_liked = bool((await session.execute(like_stmt)).scalar())

    return post, {
        "user_nickname": user_nickname,
        "user_avatar_url": user_avatar_url,
        "is_liked": is_liked,
    }


async def list_posts(
    session: AsyncSession,
    *,
    requester_user_id: uuid.UUID | None,
    category: str | None = None,
    related_ipo_code: str | None = None,
    user_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """feed 列表. 默认只返 status=published.

    特殊场景:
    - ``user_id`` 传值 → "我的发布" 列表 (返作者全部 status=published 帖,
      作者自己能看到 pending / rejected; 其他人只能看 published)
    """
    page = max(1, page)
    page_size = max(1, min(50, page_size))
    offset = (page - 1) * page_size

    base_filters: list[Any] = []
    if user_id is not None:
        base_filters.append(CommunityPost.user_id == user_id)
        # 自己看自己的"我的发布", 显示除 deleted 外全部状态
        if requester_user_id == user_id:
            base_filters.append(CommunityPost.status != "deleted")
        else:
            base_filters.append(CommunityPost.status == "published")
    else:
        # public feed, 只 published
        base_filters.append(CommunityPost.status == "published")

    if category:
        base_filters.append(CommunityPost.category == category)
    if related_ipo_code:
        base_filters.append(CommunityPost.related_ipo_code == related_ipo_code)

    # 总数
    count_stmt = select(func.count(CommunityPost.id)).where(and_(*base_filters))
    total = int((await session.execute(count_stmt)).scalar() or 0)

    # 列表 (一次 JOIN users 拉昵称/头像; like 状态用相关子查询 EXISTS 一次性)
    stmt = (
        select(
            CommunityPost,
            User.nickname,
            User.avatar_url,
        )
        .join(User, User.user_id == CommunityPost.user_id)
        .where(and_(*base_filters))
        .order_by(CommunityPost.created_at.desc())
        .limit(page_size)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).all()

    # 批量算 is_liked
    liked_ids: set[uuid.UUID] = set()
    if requester_user_id and rows:
        post_ids = [row[0].id for row in rows]
        like_stmt = select(CommunityLike.target_id).where(
            and_(
                CommunityLike.user_id == requester_user_id,
                CommunityLike.target_type == "post",
                CommunityLike.target_id.in_(post_ids),
            )
        )
        liked_rows = (await session.execute(like_stmt)).scalars().all()
        liked_ids = set(liked_rows)

    items = []
    for post, nickname, avatar_url in rows:
        items.append(
            {
                "post": post,
                "user_nickname": nickname,
                "user_avatar_url": avatar_url,
                "is_liked": post.id in liked_ids,
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ─── 删除 (软删) ───────────────────────────────────────────────────────


async def delete_post(
    session: AsyncSession,
    *,
    post_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """用户主动软删自己的帖子.

    - 帖子不存在或已 deleted → ``PostNotFoundError`` (router 转 404)
    - 帖子属于他人 → ``PostForbiddenError`` (router 转 403)
    """
    stmt = select(CommunityPost).where(CommunityPost.id == post_id)
    res = await session.execute(stmt)
    post = res.scalar_one_or_none()
    if post is None or post.status == "deleted":
        raise PostNotFoundError(f"post not found: {post_id}")
    if post.user_id != user_id:
        raise PostForbiddenError(f"post not owned by user: {post_id}")
    post.status = "deleted"
    post.visibility = "self_only"
    await session.flush()
    logger.info(f"community.post.delete user={user_id} post={post_id}")
