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
