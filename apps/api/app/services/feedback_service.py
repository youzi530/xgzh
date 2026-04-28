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
from datetime import datetime

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import RateLimitExceeded, get_redis_client
from app.db.models import Feedback
from app.schemas.feedback import FeedbackCategory, FeedbackPlatform
from app.services.compliance import scan as _compliance_scan

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

    总数与列表分两次查询; 反馈量级低不需要 cursor pagination.
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
