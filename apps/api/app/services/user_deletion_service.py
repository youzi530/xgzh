"""BE-S5-003 用户注销账号 service: 软删 + 30d 真删 cron + audit.

核心 API
========
- ``soft_delete_user(session, *, user, reason, ip, user_agent)`` — DELETE /me 路由用;
  同事务标 ``users.deleted_at = now() / status = 0`` + 写 audit 行 + 拉黑当前 access +
  吊销所有 active refresh sessions + 标 invite_code is_active=False
- ``hard_delete_pii_for_user(session, user_id)`` — cron 单用户处理; UPDATE PII 字段为 NULL
  + DELETE push_tokens + DELETE auth_sessions + 标 audit.real_purge_at = now()
- ``hard_delete_pii_overdue()`` — cron 入口; 扫 ``user_deletions.real_purge_at IS NULL
  AND requested_at < now() - grace_days``, 逐用户 hard delete
- ``run_hard_delete_pii_job()`` — APScheduler 包装

PIPL §47 路径
=============
1. 用户调 ``DELETE /api/v1/me`` (T0)
   → soft_delete_user: deleted_at=T0 / status=0 / audit row(real_purge_at=NULL) /
     当前 access 黑名单 / 所有 refresh sessions revoke
2. 30d 后 cron 跑 (T0+30d)
   → hard_delete_pii_for_user: phone/wechat_*/apple_id/nickname/avatar_url 全 NULL +
     push_tokens DELETE + auth_sessions DELETE + invite_codes 标 inactive +
     audit.real_purge_at = T0+30d

为什么保留 user_id row 不物理删?
=================================
- 财务对账: ``vip_orders`` (含 amount / paid_at) 7 年保留 (会计法), FK CASCADE 会一起删
  整笔订单 → 财务挂账; 留 user row 让 vip_orders.user_id 仍然指得到, 但里面已无 PII.
- 渠道审计: ``conversion_events`` (broker CPA / 邀请 referrer) 也走同款逻辑.
- 反馈语义: ``feedbacks.user_id`` 已配 ON DELETE SET NULL, 但保留 user row 反而能让
  老反馈记录还指着同一个匿名 user_id, admin 能看到"同一用户提了几条反馈".

清理范围 (PIPL §47 + spec/12 §BE-S5-002 PII clearance)
=======================================================

| 表 | 字段 | 处理 | 备注 |
|----|------|------|------|
| users | phone / wechat_openid / wechat_unionid / apple_id | UPDATE NULL | UNIQUE 列允许多 NULL |
| users | nickname / avatar_url | UPDATE NULL | nullable |
| users | last_active_at | 不动 | 不构成 PII (无主键关联) |
| users | region / status / deleted_at | 不动 | 已经标 status=0 + deleted_at |
| push_tokens | (整行) | DELETE | 推送通道凭据, 完全不需保留 |
| auth_sessions | (整行) | DELETE | refresh token 凭据 |
| invite_codes | is_active | UPDATE False | 防止注销用户的码被新用户绑 |
| vip_orders | (不动) | 保留 | 7 年财务留存; user_id 仍指得到, 但 user PII 已 NULL |
| conversion_events | (不动) | 保留 | 渠道审计; 同上 |
| feedbacks | (不动) | 保留 | FK 已 SET NULL |

事务边界
========
- ``soft_delete_user``: 调用方传 session, 同事务 commit (路由层 get_session 兜底); 任何
  step 失败整体回滚 (用户没注销, 凭据也没拉黑, 一致状态)
- ``hard_delete_pii_for_user``: 调用方传 session; cron 内层每用户独立事务,
  失败只跳过该用户 (不影响其它用户)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import (
    AuthSession,
    InviteCode,
    PushToken,
    User,
    UserDeletion,
)
from app.security import AccessTokenPayload, blacklist_jti


class UserDeletionError(Exception):
    """注销路径所有业务异常基类."""


class UserAlreadyDeletedError(UserDeletionError):
    """用户已注销过 (UNIQUE user_id 约束已写). 路由层映射 409."""


@dataclass(frozen=True, slots=True)
class SoftDeleteResult:
    user_id: uuid.UUID
    deleted_at: datetime
    """``users.deleted_at`` (= soft delete 时刻)"""
    real_purge_scheduled_at: datetime
    """预计真删时刻 = ``deleted_at + grace_days``; 提前告诉用户"什么时候彻底清"""
    audit_id: uuid.UUID
    """user_deletions.deletion_id, admin 审计追溯用"""


# ─── 软删 (DELETE /me 路由用) ──────────────────────────────────────


async def soft_delete_user(
    session: AsyncSession,
    *,
    user: User,
    access_payload: AccessTokenPayload,
    reason: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> SoftDeleteResult:
    """DELETE /me 服务层入口; 同事务完成软删 + audit + 凭据失效.

    步骤:
    1. INSERT user_deletions (UNIQUE user_id; 已存在 → IntegrityError 转 UserAlreadyDeletedError)
    2. UPDATE users SET deleted_at=now(), status=0
    3. UPDATE auth_sessions SET revoked_at=now() WHERE user_id=? AND revoked_at IS NULL
    4. UPDATE invite_codes SET is_active=false WHERE owner_user_id=? AND is_active=true
    5. blacklist_jti(current access jti) — 让正在用的 access token 立刻失效
       (即便 status=0 也让 deps 走 token_revoked 分支, 避免 30min access 期内的争议)

    Args:
        session: 调用方事务 (路由层 get_session)
        user: 当前登录用户 (get_current_user 已校验)
        access_payload: 当前 access token 解码后的 payload (用于 blacklist jti)
        reason: 用户填的注销原因 (256 字内, optional)
        ip: 注销请求来源 IP
        user_agent: 注销请求来源 UA

    Returns:
        SoftDeleteResult — 路由层据此响应

    Raises:
        UserAlreadyDeletedError — 同 user_id 已有 audit 行 (理论不会; user.status 检查应已挡)
    """
    settings = get_settings()
    grace_days = settings.user_deletion_grace_days
    now = datetime.now(UTC)

    # 1. INSERT audit (UNIQUE user_id 防重) — 用 ON CONFLICT DO NOTHING + RETURNING 拿 deletion_id
    insert_stmt = (
        pg_insert(UserDeletion)
        .values(
            user_id=user.user_id,
            requested_at=now,
            reason=(reason or None),
            ip_address=ip,
            user_agent=(user_agent[:256] if user_agent else None),
        )
        .on_conflict_do_nothing(constraint="uq_user_deletions_user_id")
        .returning(UserDeletion.deletion_id, UserDeletion.requested_at)
    )
    try:
        result = await session.execute(insert_stmt)
        row = result.first()
    except IntegrityError as e:
        # 不应发生 (走的是 ON CONFLICT DO NOTHING), 但兜底
        await session.rollback()
        raise UserAlreadyDeletedError(
            f"user {user.user_id} already deleted"
        ) from e

    if row is None:
        # ON CONFLICT 命中, 已有 audit 行 — 用户已经注销过, 路由层 409
        raise UserAlreadyDeletedError(
            f"user {user.user_id} already submitted deletion request"
        )

    audit_id = row[0]

    # 2. 标 user 软删 + status=0 (disabled)
    await session.execute(
        update(User)
        .where(User.user_id == user.user_id)
        .values(deleted_at=now, status=0)
    )

    # 3. 吊销所有 active refresh sessions.
    #    revoked_at 是 naive timestamp (TIMESTAMP WITHOUT TIME ZONE), 必须走 func.now()
    #    让 PG 自己生成 — 否则 asyncpg 收到 tz-aware datetime 会拒绝.
    await session.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user.user_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    )

    # 4. 标 invite_codes 不再可用 (新用户拿到该 code 调 bind_invite 会被 InviteCodeInactiveError 挡)
    await session.execute(
        update(InviteCode)
        .where(
            InviteCode.owner_user_id == user.user_id,
            InviteCode.is_active.is_(True),
        )
        .values(is_active=False)
    )

    # 5. commit DB 改动 (与 ``bind_invite`` / ``create_feedback`` 同款: service 层显式 commit;
    #    路由层 get_session 不自动 commit). 失败抛 IntegrityError 让上层 500.
    await session.commit()

    # 6. 当前 access token 立即拉黑 — 双保险 (status=0 + jti 黑名单).
    #    放 commit 之后是关键设计: DB 改动已经持久, 即便 Redis 故障也不会撤回软删.
    #    blacklist 失败仅 logger.warning, 主路径继续 (status=0 已让 user_disabled 401 兜底).
    try:
        await blacklist_jti(
            access_payload.jti,
            access_payload.expires_at,
            reason="account_deleted",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"user_deletion.blacklist_fail user_id={user.user_id} err={e!r}; "
            "fallback to status=0 only"
        )

    purge_at = now + timedelta(days=grace_days)
    logger.info(
        f"user_deletion.soft user_id={user.user_id} reason={reason!r} "
        f"requested_at={now.isoformat()} purge_at={purge_at.isoformat()}"
    )

    return SoftDeleteResult(
        user_id=user.user_id,
        deleted_at=now,
        real_purge_scheduled_at=purge_at,
        audit_id=audit_id,
    )


# ─── 30d 真删 cron ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class HardDeleteResult:
    purged_user_count: int
    purged_user_ids: tuple[uuid.UUID, ...]


async def hard_delete_pii_for_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
) -> bool:
    """单用户真删 PII; 不 commit.

    流程 (按依赖顺序):
    1. DELETE push_tokens WHERE user_id=?
    2. DELETE auth_sessions WHERE user_id=?
    3. UPDATE users SET phone/wechat_*/apple_id/nickname/avatar_url = NULL
       (保留 user_id / region / created_at / deleted_at / status — 这些不构成 PII)
    4. UPDATE user_deletions SET real_purge_at = now()

    ``invite_codes`` 在 ``soft_delete_user`` 已标 ``is_active=false``, cron 不再动.
    ``vip_orders`` / ``conversion_events`` / ``feedbacks`` 不动 (无 PII 或已 SET NULL).

    Returns:
        True 处理成功; False audit 行不存在 / 已 purge 过 (skip)
    """
    # 1. 检查 audit 行是否存在 + 未真删
    audit = (
        await session.execute(
            select(UserDeletion).where(UserDeletion.user_id == user_id)
        )
    ).scalar_one_or_none()
    if audit is None:
        logger.warning(
            f"user_deletion.hard.skip_no_audit user_id={user_id} "
            "(用户没走过 soft_delete?)"
        )
        return False
    if audit.real_purge_at is not None:
        logger.debug(
            f"user_deletion.hard.skip_already_purged user_id={user_id} "
            f"prev_purge_at={audit.real_purge_at.isoformat()}"
        )
        return False

    now = datetime.now(UTC)

    # 2. 删 push tokens (整行)
    await session.execute(delete(PushToken).where(PushToken.user_id == user_id))

    # 3. 删 auth sessions (整行)
    await session.execute(delete(AuthSession).where(AuthSession.user_id == user_id))

    # 4. 清 users PII 字段
    await session.execute(
        update(User)
        .where(User.user_id == user_id)
        .values(
            phone=None,
            wechat_openid=None,
            wechat_unionid=None,
            apple_id=None,
            nickname=None,
            avatar_url=None,
        )
    )

    # 5. 标 audit 真删完成
    await session.execute(
        update(UserDeletion)
        .where(UserDeletion.user_id == user_id)
        .values(real_purge_at=now)
    )

    logger.info(f"user_deletion.hard.ok user_id={user_id} purge_at={now.isoformat()}")
    return True


async def hard_delete_pii_overdue() -> HardDeleteResult:
    """cron 入口: 扫所有 ``real_purge_at IS NULL AND requested_at < now()-grace_days`` 的
    audit 行, 逐个真删.

    隔离:
    - 每个用户独立事务 (开新 session); 单用户失败不影响其他用户继续跑
    - cron job 整体失败兜底 (run_hard_delete_pii_job 用 try/except)
    """
    settings = get_settings()
    grace_days = settings.user_deletion_grace_days
    cutoff = datetime.now(UTC) - timedelta(days=grace_days)
    factory = get_session_factory()

    # 1. 先列出待真删 user_ids (短事务)
    async with factory() as session:
        rows = (
            await session.execute(
                select(UserDeletion.user_id)
                .where(
                    UserDeletion.real_purge_at.is_(None),
                    UserDeletion.requested_at < cutoff,
                )
                .order_by(UserDeletion.requested_at)
                .limit(1000)  # 单次 cron 最多处理 1000 个; 5.5+ 量级再调
            )
        ).scalars().all()

    if not rows:
        logger.debug(f"user_deletion.cron.empty cutoff={cutoff.isoformat()}")
        return HardDeleteResult(purged_user_count=0, purged_user_ids=())

    purged: list[uuid.UUID] = []
    for user_id in rows:
        # 每用户独立事务: 失败不影响其他
        try:
            async with factory() as session, session.begin():
                ok = await hard_delete_pii_for_user(session, user_id=user_id)
            if ok:
                purged.append(user_id)
        except Exception as e:  # noqa: BLE001
            logger.exception(
                f"user_deletion.cron.user_failed user_id={user_id} err={e!r}"
            )

    logger.info(
        f"user_deletion.cron.swept count={len(purged)}/{len(rows)} "
        f"cutoff={cutoff.isoformat()}"
    )
    return HardDeleteResult(
        purged_user_count=len(purged),
        purged_user_ids=tuple(purged),
    )


async def run_hard_delete_pii_job() -> None:
    """APScheduler 包装 (与 ``run_expire_overdue_job`` 同款).

    任何异常都 logger.exception 不抛, 让 scheduler 不会把 job 标 misfire (我们希望它
    继续按 cron 跑而不是被踢掉).
    """
    try:
        await hard_delete_pii_overdue()
    except Exception as e:  # noqa: BLE001
        logger.exception(f"user_deletion.cron.job_failed: {e}")


__all__ = [
    "HardDeleteResult",
    "SoftDeleteResult",
    "UserAlreadyDeletedError",
    "UserDeletionError",
    "hard_delete_pii_for_user",
    "hard_delete_pii_overdue",
    "run_hard_delete_pii_job",
    "soft_delete_user",
]
