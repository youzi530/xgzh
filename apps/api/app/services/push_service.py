"""推送 token 业务服务 (BE-011).

接口:
- :func:`register_token`    幂等注册 / 覆盖 token (PG ON CONFLICT DO UPDATE)
- :func:`unregister_token`  幂等注销 (单 SQL DELETE; 0 行也返 200)
- :func:`list_user_tokens`  取当前用户全部活跃 token, 给后续推送服务用
                            (Sprint 4 推送实施时会调; 本 Sprint 不暴露成 API)

设计要点:
1. **token 不回显客户端**: APNs / FCM token 是敏感凭据, 后端写成功就足够,
   不需要 echo 回去给客户端 (客户端本来就持有). 减少泄露面.
2. **ON CONFLICT 用 ``RETURNING (xmax = 0)`` 区分新增 / 覆盖**: 与 BE-010 同款
   PG trick, 省一次 SELECT round-trip.
3. **覆盖时强制 ``is_active = true``**: 用户卸载后再装回, ``unregister_token``
   把行删掉; 但万一未来引入 "运营禁用" 把 ``is_active`` 置 false, 用户重新
   注册同 device_id 时应该自动重新激活.
4. **DELETE 用复合条件 ``(user_id, platform, device_id)``**: 杜绝越权删别人的 token.
   即便 device_id 由客户端控制, 也只能影响"绑到自己 user_id 的设备记录",
   不会跨用户污染.
5. ``list_user_tokens`` 只返回 ``is_active=true`` 的行: 推送实施时不会向被禁用
   token 发送, 减少无效请求.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PushToken
from app.schemas.push import PushPlatform


@dataclass(frozen=True, slots=True)
class PushTokenRegisterResult:
    id: int
    platform: PushPlatform
    device_id: str
    is_active: bool
    created: bool
    registered_at: datetime


async def register_token(
    session: AsyncSession,
    *,
    user_id: UUID,
    platform: PushPlatform,
    token: str,
    device_id: str,
) -> PushTokenRegisterResult:
    """幂等注册. 同一 ``(user_id, platform, device_id)`` 复发只刷新 ``token`` + 重新激活."""
    stmt = (
        pg_insert(PushToken)
        .values(
            user_id=user_id,
            platform=platform,
            token=token,
            device_id=device_id,
            is_active=True,
        )
        .on_conflict_do_update(
            index_elements=["user_id", "platform", "device_id"],
            set_={"token": token, "is_active": True},
        )
        .returning(
            PushToken.id,
            PushToken.is_active,
            PushToken.created_at,
            PushToken.updated_at,
            sa_text("(xmax = 0)"),
        )
    )

    row = (await session.execute(stmt)).one()
    pk: int = row[0]
    is_active: bool = row[1]
    created_at: datetime = row[2]
    updated_at: datetime = row[3]
    created: bool = bool(row[4])
    await session.commit()

    logger.info(
        f"push.register user_id={user_id} platform={platform} device_id={device_id} "
        f"id={pk} created={created}"
    )
    return PushTokenRegisterResult(
        id=pk,
        platform=platform,
        device_id=device_id,
        is_active=is_active,
        created=created,
        registered_at=created_at if created else updated_at,
    )


async def unregister_token(
    session: AsyncSession,
    *,
    user_id: UUID,
    platform: PushPlatform,
    device_id: str,
) -> bool:
    """幂等注销. 返回 ``removed: bool``; ``False`` 表示本来就没注册."""
    result = await session.execute(
        delete(PushToken).where(
            PushToken.user_id == user_id,
            PushToken.platform == platform,
            PushToken.device_id == device_id,
        )
    )
    await session.commit()
    removed = (result.rowcount or 0) > 0
    logger.info(
        f"push.unregister user_id={user_id} platform={platform} device_id={device_id} "
        f"removed={removed}"
    )
    return removed


async def list_user_tokens(
    session: AsyncSession, *, user_id: UUID
) -> list[PushToken]:
    """取当前用户全部活跃 token. Sprint 4 推送实施时调."""
    rows = await session.execute(
        select(PushToken)
        .where(PushToken.user_id == user_id, PushToken.is_active.is_(True))
        .order_by(PushToken.created_at.desc())
    )
    return list(rows.scalars().all())


__all__ = [
    "PushTokenRegisterResult",
    "list_user_tokens",
    "register_token",
    "unregister_token",
]
