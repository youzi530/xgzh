"""User 业务服务（骨架）.

BE-001 仅需 ``find_by_phone`` 给后续 BE-002 (OTP 校验 + 注册/登录) 用。
注册/登录、邀请码绑定、JWT 颁发都是 BE-002 / BE-006 的职责, 此处不做。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


async def find_user_by_phone(session: AsyncSession, phone: str) -> User | None:
    """按 phone 精确查找. 软删 (deleted_at) 用户视为不存在."""
    stmt = (
        select(User)
        .where(User.phone == phone)
        .where(User.deleted_at.is_(None))
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def find_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    stmt = select(User).where(User.user_id == user_id).where(User.deleted_at.is_(None))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def find_user_by_wechat_unionid(
    session: AsyncSession, unionid: str
) -> User | None:
    """优先按 unionid 查 — 同一开放平台下跨小程序/公众号的稳定身份."""
    stmt = (
        select(User)
        .where(User.wechat_unionid == unionid)
        .where(User.deleted_at.is_(None))
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def find_user_by_wechat_openid(
    session: AsyncSession, openid: str
) -> User | None:
    """openid 仅在单一小程序内稳定; 没拿到 unionid 时 fallback 用."""
    stmt = (
        select(User)
        .where(User.wechat_openid == openid)
        .where(User.deleted_at.is_(None))
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()
