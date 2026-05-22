"""反馈业务服务 (Sprint 5 BE-S5-004).

接口:
- :func:`enforce_rate_limit`  按"匿名 IP / 5 min ≤ 3"或"登录 user / 1h ≤ 10"做配额
- :func:`create_feedback`     落 PG, 返 (feedback_id, created_at)
- :func:`list_feedbacks`      admin 分页 + filter (category / platform), 返 items + total

设计要点
========

1. **限流双策略**: spec/12 §AC 写明匿名 / 登录两套配额, 用一个 helper 在路由层
   先调一次, 不用 ``@rate_limit`` 装饰器 (因为它无法根据请求是否登录切配额).
   key 走 ``cache.namespaced_key``, 复用全局 Redis 客户端 + 全局 ``RateLimitExceeded``
   handler (main.py 里转 429 + Retry-After).
2. **匿名场景 user_id=NULL**: ORM 已设 nullable + FK SET NULL, 注销用户的反馈
   保留 (产品分析用) 但脱钩.
3. **content 红线词扫描**: BE-S5-001 红线词词典已上, 这里 **不**阻断匿名反馈
   (用户可能就是要吐槽 "你们的 AI 说了必涨!"); 仅 logger.warning 上报 metric,
   admin 在面板看到原文判断.
4. **admin list 按 created_at DESC**: alembic 0009 已建 DESC 索引, 命中.
5. **总数与列表分两次查询**: ``count()`` + ``select() .limit().offset()``.
   反馈量级低 (估 < 100/天), 不用 cursor pagination.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.cache import RateLimitExceeded, get_redis_client
from app.db.models import Feedback, User
from app.schemas.feedback import FeedbackCategory, FeedbackPlatform
from app.services.compliance import scan as _compliance_scan

# Sprint 11 BE-S11-B01: admin 处理状态枚举
AdminFeedbackStatus = Literal["pending", "reviewed", "resolved", "closed"]


class FeedbackNotFoundError(Exception):
    """目标 feedback_id 不存在或已硬删 (软删的不抛, 由 include_deleted 控制)."""

# 限流配额 (spec/12 §BE-S5-004 AC)
_ANON_RATE_TIMES = 3
_ANON_RATE_WINDOW = 5 * 60  # 5 min
_USER_RATE_TIMES = 10
_USER_RATE_WINDOW = 60 * 60  # 1 h


@dataclass(frozen=True, slots=True)
class FeedbackCreated:
    """``create_feedback`` 返回值."""

    feedback_id: uuid.UUID
    created_at: datetime


async def enforce_rate_limit(
    *, user_id: uuid.UUID | None, client_ip: str | None
) -> None:
    """按"是否登录"切配额, 超限直接 raise :class:`RateLimitExceeded` (端层转 429).

    - 登录: ``rate:feedback:user:{user_id}`` ≤ 10 / 1h
    - 匿名: ``rate:feedback:ip:{client_ip}``  ≤ 3 / 5min
    - 匿名且没有 IP (本地测试 / proxy 透传缺失): 走一个 fallback bucket
      ``rate:feedback:ip:_unknown``, 防止"没 IP 就无限刷"; 真生产要靠
      proxy 配置 ``X-Forwarded-For`` 把 IP 透出来.
    """
    client = get_redis_client()
    if user_id is not None:
        key = f"rate:feedback:user:{user_id}"
        cap = _USER_RATE_TIMES
        per = _USER_RATE_WINDOW
    else:
        ip = client_ip or "_unknown"
        key = f"rate:feedback:ip:{ip}"
        cap = _ANON_RATE_TIMES
        per = _ANON_RATE_WINDOW

    current = await client.incr_with_expire(key, per)
    if current > cap:
        ttl = await client.ttl(key)
        retry_after = ttl if ttl > 0 else per
        logger.info(
            f"feedback.rate_limit_exceeded key={key} current={current} cap={cap}"
        )
        raise RateLimitExceeded(
            key=key,
            times=cap,
            per_seconds=per,
            retry_after=retry_after,
        )


async def create_feedback(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None,
    category: FeedbackCategory,
    content: str,
    platform: FeedbackPlatform,
    contact: str | None = None,
    app_version: str | None = None,
    client_ip: str | None = None,
) -> FeedbackCreated:
    """落 PG, 返 (feedback_id, created_at).

    红线词扫描仅 logger.warning, **不**阻断 — 用户有权"吐槽 AI 说了必涨".
    """
    scan_result = _compliance_scan(content)
    if scan_result.has_tier1 or scan_result.has_tier2:
        logger.warning(
            "feedback.compliance_words_in_content "
            f"tier1={scan_result.tier1_hits} tier2={scan_result.tier2_hits} "
            f"user_id={user_id}"
        )

    row = Feedback(
        user_id=user_id,
        category=category,
        content=content,
        contact=contact,
        app_version=app_version,
        platform=platform,
        ip_inet=client_ip,
    )
    session.add(row)
    await session.flush()  # 拿 server_default 生成的 feedback_id / created_at
    await session.refresh(row)

    logger.info(
        f"feedback.created id={row.feedback_id} category={category} "
        f"platform={platform} user_id={user_id} ip={client_ip}"
    )

    return FeedbackCreated(
        feedback_id=row.feedback_id,
        created_at=row.created_at,
    )


async def list_feedbacks(
    session: AsyncSession,
    *,
    category: FeedbackCategory | None = None,
    platform: FeedbackPlatform | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Feedback], int]:
    """admin 列表 + filter, 返 (items, total).

    Sprint 5 ops 通道 (X-Admin-Token) 用 — 行为保持: 不过滤软删, limit/offset 分页.
    """
    base_filters = []
    if category is not None:
        base_filters.append(Feedback.category == category)
    if platform is not None:
        base_filters.append(Feedback.platform == platform)

    count_stmt = select(func.count()).select_from(Feedback)
    list_stmt = select(Feedback).order_by(Feedback.created_at.desc())
    if base_filters:
        for f in base_filters:
            count_stmt = count_stmt.where(f)
            list_stmt = list_stmt.where(f)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (
        (await session.execute(list_stmt.limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return list(rows), int(total)


# ─── Sprint 11 BE-S11-B01: admin 工作流 service ──────────────────────


async def admin_list_feedbacks(
    session: AsyncSession,
    *,
    q: str | None = None,
    category: FeedbackCategory | None = None,
    platform: FeedbackPlatform | None = None,
    admin_status: AdminFeedbackStatus | None = None,
    include_deleted: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Feedback], int]:
    """admin JWT 路径分页 + 多维 filter; 返 (items, total).

    与老 ops ``list_feedbacks`` 的区别:
    - page/page_size 分页 (跟 admin_users 一致), 老路径 limit/offset 保持
    - 默认隐藏软删 (deleted_at NOT NULL), 想看显式 ``include_deleted=true``
    - 加 ``q`` 模糊搜 content/contact (ilike)
    - 加 ``admin_status`` filter (NULL 视为 "pending")
    """
    base_filters: list[ColumnElement[bool]] = []
    if not include_deleted:
        base_filters.append(Feedback.deleted_at.is_(None))
    if category is not None:
        base_filters.append(Feedback.category == category)
    if platform is not None:
        base_filters.append(Feedback.platform == platform)
    if admin_status is not None:
        if admin_status == "pending":
            # NULL or "pending" 都视为 pending (省 backfill)
            base_filters.append(
                (Feedback.admin_status.is_(None))
                | (Feedback.admin_status == "pending")
            )
        else:
            base_filters.append(Feedback.admin_status == admin_status)
    if q:
        like = f"%{q.strip()}%"
        base_filters.append(
            Feedback.content.ilike(like) | Feedback.contact.ilike(like)
        )

    count_stmt = select(func.count()).select_from(Feedback)
    list_stmt = select(Feedback).order_by(Feedback.created_at.desc())
    for f in base_filters:
        count_stmt = count_stmt.where(f)
        list_stmt = list_stmt.where(f)

    total = (await session.execute(count_stmt)).scalar_one()
    offset = max(page - 1, 0) * page_size
    rows = (
        (await session.execute(list_stmt.limit(page_size).offset(offset)))
        .scalars()
        .all()
    )
    return list(rows), int(total)


async def admin_get_feedback(
    session: AsyncSession,
    feedback_id: uuid.UUID,
    *,
    include_deleted: bool = True,
) -> Feedback:
    """admin 视角查单 feedback. 默认 ``include_deleted=True`` (排查软删的需要).

    Raises:
        FeedbackNotFoundError: id 物理不存在 (即便 include_deleted=True 也查不到)
    """
    stmt = select(Feedback).where(Feedback.feedback_id == feedback_id)
    if not include_deleted:
        stmt = stmt.where(Feedback.deleted_at.is_(None))
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise FeedbackNotFoundError(f"feedback_id={feedback_id} not found")
    return row


async def update_feedback(
    session: AsyncSession,
    *,
    admin: User,
    feedback_id: uuid.UUID,
    admin_status: AdminFeedbackStatus | None = None,
    admin_note: str | None = None,
) -> Feedback:
    """admin 改 feedback 处理状态 + 内部备注.

    不能改 ``content`` (用户原文不可篡改; PIPL 合规风险), 路由层 schema 已隔离.
    传 None 视为不动该字段 (跟 Pydantic ``exclude_unset`` 配合).

    每次 admin_status 变化都更新 ``reviewed_by`` / ``reviewed_at`` (记录最后处理人).
    admin_note 单独改不动 reviewed_* (admin 只是补备注, 不算"处理过").

    Raises:
        FeedbackNotFoundError: id 不存在或已软删 (软删的禁改; admin 想改先恢复)
    """
    row = (
        await session.execute(
            select(Feedback).where(
                Feedback.feedback_id == feedback_id,
                Feedback.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise FeedbackNotFoundError(
            f"feedback_id={feedback_id} not found or already soft-deleted"
        )

    changed = []
    if admin_status is not None and row.admin_status != admin_status:
        row.admin_status = admin_status
        row.reviewed_by = admin.user_id
        row.reviewed_at = datetime.now(UTC)
        changed.extend(["admin_status", "reviewed_by", "reviewed_at"])
    if admin_note is not None:
        # 空字符串视为清备注
        new_note = admin_note if admin_note.strip() else None
        if row.admin_note != new_note:
            row.admin_note = new_note
            changed.append("admin_note")

    if not changed:
        logger.info(f"feedback.admin_update.noop id={feedback_id} admin={admin.user_id}")
        return row

    await session.commit()
    await session.refresh(row)
    logger.warning(
        f"feedback.admin_update.ok id={feedback_id} admin={admin.user_id} fields={changed}"
    )
    return row


async def soft_delete_feedback(
    session: AsyncSession,
    *,
    admin: User,
    feedback_id: uuid.UUID,
) -> Feedback:
    """软删 feedback (deleted_at = NOW). 已删的视为幂等成功.

    Raises:
        FeedbackNotFoundError: id 物理不存在
    """
    row = (
        await session.execute(
            select(Feedback).where(Feedback.feedback_id == feedback_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise FeedbackNotFoundError(f"feedback_id={feedback_id} not found")
    if row.deleted_at is None:
        row.deleted_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(row)
        logger.warning(
            f"feedback.admin_soft_delete.ok id={feedback_id} admin={admin.user_id}"
        )
    else:
        logger.info(
            f"feedback.admin_soft_delete.noop id={feedback_id} admin={admin.user_id} already_deleted"
        )
    return row


async def restore_feedback(
    session: AsyncSession,
    *,
    admin: User,
    feedback_id: uuid.UUID,
) -> Feedback:
    """恢复软删 (deleted_at=NULL). 没软删的 noop.

    Raises:
        FeedbackNotFoundError: id 物理不存在
    """
    row = (
        await session.execute(
            select(Feedback).where(Feedback.feedback_id == feedback_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise FeedbackNotFoundError(f"feedback_id={feedback_id} not found")
    if row.deleted_at is not None:
        row.deleted_at = None
        await session.commit()
        await session.refresh(row)
        logger.warning(
            f"feedback.admin_restore.ok id={feedback_id} admin={admin.user_id}"
        )
    return row
