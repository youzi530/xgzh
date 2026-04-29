"""社区反 spam 限流 (BE-S6-009).

策略 (spec/13)
==============
| 行为     | 限流                           |
|----------|--------------------------------|
| 发帖     | 60s ≤ 1 / 24h ≤ 10              |
| 评论     | 10s ≤ 1 / 24h ≤ 50              |
| 点赞     | 1s ≤ 5                          |
| 举报     | 60s ≤ 1 / 24h ≤ 5               |
| 新用户 7d | 只读 (不能发帖 / 评论)         |

实现说明
========
- 复用 spec/12 BE-S5-006 ``RateLimiter`` (incr_with_expire)
- 多维度限流: 同一行为可能既受 60s 又受 24h 限制 — 任一命中即 reject
- "新用户 7d 只读" 走 ``user.created_at`` 检查而非 Redis 限流; service 层先调
  :func:`enforce_new_user_writable` 再调具体限流器

异常
====
- 命中限流 → ``RateLimitExceeded`` (复用 ``app.cache``)
- 新用户禁写 → ``NewUserReadOnlyError``
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import RateLimitExceeded, get_redis_client
from app.db.models import User

# 限流配额 (spec/13 §BE-S6-009 AC)
_POST_RATE_60S = 1
_POST_RATE_24H = 10
_COMMENT_RATE_10S = 1
_COMMENT_RATE_24H = 50
_LIKE_RATE_1S = 5
_REPORT_RATE_60S = 1
_REPORT_RATE_24H = 5

# 新用户保护期 (天)
_NEW_USER_READONLY_DAYS = 7


class NewUserReadOnlyError(Exception):
    """新用户 7d 内禁止发帖 / 评论."""

    def __init__(self, *, user_id: uuid.UUID, account_age_seconds: int) -> None:
        self.user_id = user_id
        self.account_age_seconds = account_age_seconds
        super().__init__(
            f"new user readonly: user={user_id} "
            f"age_s={account_age_seconds}"
        )


async def enforce_new_user_writable(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> None:
    """检查用户是否度过 ``_NEW_USER_READONLY_DAYS`` 保护期.

    新注册用户 7d 内只能浏览, 不能发帖 / 评论 (反 spam 黑产用大批量新号灌水).
    """
    stmt = select(User.created_at).where(User.user_id == user_id)
    result = await session.execute(stmt)
    created_at = result.scalar_one_or_none()
    if created_at is None:
        # 用户不存在 (理论上不该到这, 上层 auth 已守); raise 让上层 401
        raise NewUserReadOnlyError(user_id=user_id, account_age_seconds=0)

    now = datetime.now(UTC)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    age = now - created_at
    if age < timedelta(days=_NEW_USER_READONLY_DAYS):
        logger.info(
            f"community.new_user_readonly user={user_id} age_s={age.total_seconds()}"
        )
        raise NewUserReadOnlyError(
            user_id=user_id,
            account_age_seconds=int(age.total_seconds()),
        )


async def _enforce_window(
    *,
    key: str,
    limit: int,
    window_seconds: int,
) -> None:
    """通用 window-based 限流. 命中 raise ``RateLimitExceeded``."""
    client = get_redis_client()
    current = await client.incr_with_expire(key, window_seconds)
    if current > limit:
        ttl = await client.ttl(key)
        retry_after = ttl if ttl > 0 else window_seconds
        raise RateLimitExceeded(
            key=key,
            times=limit,
            per_seconds=window_seconds,
            retry_after=retry_after,
        )


async def enforce_post_rate(*, user_id: uuid.UUID) -> None:
    """发帖限流: 60s ≤ 1 / 24h ≤ 10."""
    await _enforce_window(
        key=f"rate:community_post:60s:user:{user_id}",
        limit=_POST_RATE_60S,
        window_seconds=60,
    )
    await _enforce_window(
        key=f"rate:community_post:24h:user:{user_id}",
        limit=_POST_RATE_24H,
        window_seconds=86400,
    )


async def enforce_comment_rate(*, user_id: uuid.UUID) -> None:
    """评论限流: 10s ≤ 1 / 24h ≤ 50."""
    await _enforce_window(
        key=f"rate:community_comment:10s:user:{user_id}",
        limit=_COMMENT_RATE_10S,
        window_seconds=10,
    )
    await _enforce_window(
        key=f"rate:community_comment:24h:user:{user_id}",
        limit=_COMMENT_RATE_24H,
        window_seconds=86400,
    )


async def enforce_like_rate(*, user_id: uuid.UUID) -> None:
    """点赞限流: 1s ≤ 5 次 (防快速 like 刷榜)."""
    await _enforce_window(
        key=f"rate:community_like:1s:user:{user_id}",
        limit=_LIKE_RATE_1S,
        window_seconds=1,
    )


async def enforce_report_rate(*, user_id: uuid.UUID) -> None:
    """举报限流: 60s ≤ 1 / 24h ≤ 5 (防恶意举报)."""
    await _enforce_window(
        key=f"rate:community_report:60s:user:{user_id}",
        limit=_REPORT_RATE_60S,
        window_seconds=60,
    )
    await _enforce_window(
        key=f"rate:community_report:24h:user:{user_id}",
        limit=_REPORT_RATE_24H,
        window_seconds=86400,
    )
