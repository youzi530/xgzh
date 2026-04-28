"""邀请码业务 (BE-006).

两个职责:

1. **注册时同事务落 ``invite_codes`` 行**: ``register_invite_code_for_user``
   - BE-002/BE-005 在 ``_create_user_with_*`` 已经给 ``users.invite_code`` 生成了 8 字符大写+数字
     (``62^8 ≈ 2.18e14`` 空间, 远超 backlog 写的 6 位 base62 ``56e9``, 不必降级)。
   - 本服务把那个 code 同事务镜像到 ``invite_codes`` 表, 让活动期/KOL 渠道用统一接口管理
     (``usage_count`` / ``max_usage`` / ``is_active`` / ``expires_at`` / ``note``)。

2. **绑定 referrer**: ``bind_invite``
   - 一次性 (``users.invited_by`` 一旦写入不可改, 通过 ``WHERE invited_by IS NULL`` 条件 UPDATE
     拿 ``rowcount`` 防双绑)
   - 自禁 (不能绑自己)
   - 校验 invite_code 可用 (active / 未过期 / 未达 max_usage)
   - 不允许绑 ``owner_user_id IS NULL`` 的运营码 (运营码只用于"我注册时来自哪个活动"埋点,
     不进 referrer 链, 留待 BE-006 之后的"渠道码"功能)
   - 并发安全: 用 ``SELECT ... FOR UPDATE`` 锁住 invite_codes 行避免 ``usage_count`` 计错

异常枚举 (路由层映射):

| service exception                | route status | detail.code              |
|----------------------------------|:------------:|--------------------------|
| ``InviteCodeNotFoundError``      | 404          | ``invite_code_not_found``|
| ``InviteCodeInactiveError``      | 400          | ``invite_code_inactive`` |
| ``InviteCodeExpiredError``       | 400          | ``invite_code_expired``  |
| ``InviteCodeExhaustedError``     | 400          | ``invite_code_exhausted``|
| ``InviteCodeNotPersonalError``   | 400          | ``invite_code_not_personal``|
| ``InviteSelfBindError``          | 400          | ``invite_self_binding``  |
| ``InviteAlreadyBoundError``      | 400          | ``invite_already_bound`` |
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import InviteCode, InviteReward, User

# ----------------------------- exceptions -----------------------------


class InviteError(Exception):
    """所有邀请码相关业务异常的统一基类."""


class InviteCodeNotFoundError(InviteError):
    """``invite_codes`` 表里没这个 code (用户输错 / 还没生成)."""


class InviteCodeInactiveError(InviteError):
    """code 被运营禁用 (``is_active = false``)."""


class InviteCodeExpiredError(InviteError):
    """code 已过 ``expires_at``."""


class InviteCodeExhaustedError(InviteError):
    """code 已达到 ``max_usage`` (个人码默认 NULL = 无限, 运营码常设上限)."""


class InviteCodeNotPersonalError(InviteError):
    """code 是运营码 (``owner_user_id IS NULL``), MVP 不支持当 referrer 绑."""


class InviteSelfBindError(InviteError):
    """用户输了自己的 invite_code (即便理论上 owner = self_id, 也直接拒)."""


class InviteAlreadyBoundError(InviteError):
    """用户 ``invited_by`` 已写过, 一次性, 不再覆盖."""


# ----------------------------- DTOs -----------------------------


@dataclass(frozen=True, slots=True)
class InviteBindResult:
    referrer_user_id: uuid.UUID
    referrer_invite_code: str
    new_usage_count: int


@dataclass(frozen=True, slots=True)
class InviteRewardResult:
    """``apply_invite_reward`` 返回; 路由层不直接暴露, 主要给单测断言用.

    Fields:
        triggered: 本次调用是否真的触发了奖励 (false 含义: 没达阈值 / 已发过 / 关闭奖励)
        successful_invitee_count: 当前累计成功被邀请数
        threshold_n: 配置的触发阈值 (settings.invite_reward_n_users)
        vip_days_granted: 配置的奖励天数 (settings.invite_reward_vip_days); 即便未触发也带回, 让前端能渲染"还差 N 个达成 +M 天"
    """

    triggered: bool
    successful_invitee_count: int
    threshold_n: int
    vip_days_granted: int


# ----------------------------- 注册时落 invite_codes 行 -----------------------------


async def register_invite_code_for_user(session: AsyncSession, user: User) -> InviteCode:
    """在用户注册的同一事务里, 把 ``user.invite_code`` 镜像到 ``invite_codes`` 表.

    调用方应该已经 ``session.flush()`` 过 ``user`` (拿到 user_id 才能写 owner_user_id)。
    本函数只 ``add + flush``, 不 commit, 由调用方控事务边界。

    个人邀请码默认: ``max_usage = NULL`` (无上限), ``is_active = true``, ``expires_at = NULL``
    (永久), ``note = "personal"``。后续运营如果想全量降级 (例如关掉某个用户的邀请功能),
    直接 UPDATE ``is_active = false`` 即可, 不影响该用户登录。
    """
    invite_row = InviteCode(
        code=user.invite_code,
        owner_user_id=user.user_id,
        usage_count=0,
        max_usage=None,
        is_active=True,
        expires_at=None,
        note="personal",
    )
    session.add(invite_row)
    await session.flush()
    return invite_row


# ----------------------------- 绑定 -----------------------------


def _now_utc() -> datetime:
    return datetime.now(UTC)


async def _load_invite_for_update(
    session: AsyncSession, code: str
) -> InviteCode:
    """``SELECT ... FOR UPDATE`` 锁住 invite_codes 行后做可用性检查."""
    stmt = (
        select(InviteCode)
        .where(InviteCode.code == code)
        .with_for_update()
    )
    invite = (await session.execute(stmt)).scalar_one_or_none()
    if invite is None:
        raise InviteCodeNotFoundError(f"invite code {code!r} not found")

    if not invite.is_active:
        raise InviteCodeInactiveError(f"invite code {code!r} is inactive")

    if invite.expires_at is not None:
        # invite.expires_at 落库时 PG 会按 timestamptz 处理; 但 SQLAlchemy 默认 Column
        # 是 naive datetime. 兼容两种: naive 视作 UTC.
        exp = invite.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp <= _now_utc():
            raise InviteCodeExpiredError(f"invite code {code!r} expired at {exp.isoformat()}")

    if invite.max_usage is not None and invite.usage_count >= invite.max_usage:
        raise InviteCodeExhaustedError(
            f"invite code {code!r} usage {invite.usage_count}/{invite.max_usage}"
        )

    if invite.owner_user_id is None:
        # 运营码: 留给后续"渠道追踪"功能, MVP 不支持作为 referrer
        raise InviteCodeNotPersonalError(
            f"invite code {code!r} has no owner; channel codes are not bindable in MVP"
        )

    return invite


async def bind_invite(
    session: AsyncSession,
    *,
    current_user: User,
    code: str,
) -> InviteBindResult:
    """给 ``current_user`` 绑定 referrer (one-shot, 一次性).

    成功时 commit; 失败时不 commit, 由 ``get_session`` 在异常路径 rollback。
    """
    code = code.strip().upper()

    # 1) 自禁 — 提前拦, 不必去碰 invite_codes 表
    if code == (current_user.invite_code or "").upper():
        raise InviteSelfBindError("cannot bind to own invite code")

    # 2) 已经绑过 — 业务上一次性, fast-fail; 但仍需要 lock-and-check 防并发
    if current_user.invited_by is not None:
        raise InviteAlreadyBoundError(
            f"user {current_user.user_id} already bound to {current_user.invited_by}"
        )

    # 3) 锁 invite_codes 行 + 校验
    invite = await _load_invite_for_update(session, code)
    assert invite.owner_user_id is not None  # _load_invite_for_update 已校验过

    # 4) owner != self (覆盖 invite_code 改动后罕见的 self 路径)
    if invite.owner_user_id == current_user.user_id:
        raise InviteSelfBindError("invite code belongs to self")

    # 5) 用 conditional UPDATE 防并发双绑: rowcount = 0 → 期间被另一请求写过
    update_stmt = (
        update(User)
        .where(User.user_id == current_user.user_id)
        .where(User.invited_by.is_(None))
        .values(invited_by=invite.owner_user_id)
    )
    result = await session.execute(update_stmt)
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise InviteAlreadyBoundError(
            f"user {current_user.user_id} concurrently bound by another request"
        )

    # 6) usage_count += 1 (在 FOR UPDATE 锁内, 不会 race)
    invite.usage_count += 1
    await session.flush()

    referrer_user_id = invite.owner_user_id

    await session.commit()
    # 注意: ORM 实例已在 expire_on_commit=False 的 session 中, 但保险起见用本地变量
    new_count = invite.usage_count

    logger.info(
        f"invite.bind.ok user_id={current_user.user_id} referrer={referrer_user_id} "
        f"code={code} usage={new_count}"
    )

    # BE-S5-005: bind 成功 → 异步触发邀请奖励 (不阻断主路径; 失败仅 logger.exception).
    # 用独立事务 (开新 session) 而不是复用 ``session``, 因为 bind 已 commit, 这里失败
    # 不应回滚 bind. 任何异常都吞掉 — 用户成功绑定 referrer 是核心成功语义,
    # 奖励是次要副作用; 失败可由 admin 后续手动补发.
    try:
        await _apply_invite_reward_outside_txn(referrer_user_id)
    except Exception as e:  # noqa: BLE001
        logger.exception(
            f"invite.reward.fail inviter={referrer_user_id} err={e!r}"
        )

    return InviteBindResult(
        referrer_user_id=referrer_user_id,
        referrer_invite_code=code,
        new_usage_count=new_count,
    )


# ─── BE-S5-005 邀请有礼 trigger ────────────────────────────────────────


async def _apply_invite_reward_outside_txn(inviter_user_id: uuid.UUID) -> InviteRewardResult:
    """``bind_invite`` commit 后调; 起独立 session 把奖励算清楚.

    与 ``apply_invite_reward`` 区别: 这个 helper 自己开 session + commit, 让 bind_invite
    主路径不需要传 session 进来. 单测可以直接调 ``apply_invite_reward(session, ...)`` 走
    同事务路径.
    """
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        result = await apply_invite_reward(session, inviter_user_id=inviter_user_id)
        await session.commit()
        return result


async def apply_invite_reward(
    session: AsyncSession,
    *,
    inviter_user_id: uuid.UUID,
) -> InviteRewardResult:
    """统计 inviter 累计成功被邀请数, 达阈值则给 inviter 延 VIP + 写 audit.

    "成功被邀请"定义 (spec/12 §防刷):
    - 被邀请人 ``users.invited_by = inviter_user_id``
    - 被邀请人 ``status = 1`` (active, 未禁用 / 未注销)
    - 被邀请人 ``deleted_at IS NULL`` (BE-S5-003 注销后软删, 不算邀请数)
    - 被邀请人 ``phone IS NOT NULL`` (微信注册无 phone 也算 — 用 ``user_id`` 唯一; 同手机号
      防刷在 BE-S5-003 SoftDelete + ``users.phone`` UNIQUE 已隐式保证)

    幂等:
    - audit 表 UNIQUE (inviter, threshold_n) — INSERT ... ON CONFLICT DO NOTHING
    - 同一阈值并发 / 重试调 → 只写一次, 只 extend 一次

    奖励路径:
    1. 数被邀请数 N
    2. 不达阈值 → ``triggered=False`` 直接返回, 不动 audit / vip
    3. 阈值已发过 (audit 命中) → ``triggered=False`` 静默跳过 (前端可以显示"已达成 +7d 已发放")
    4. 阈值首次达成 → INSERT audit (rowcount=1 才进下一步) + extend_membership(+N days)

    Args:
        session: 调用方传 (或 ``_apply_invite_reward_outside_txn`` 自起新事务)
        inviter_user_id: 邀请人 ID

    Returns:
        InviteRewardResult — 含 triggered / 当前邀请数 / 阈值 / 奖励天数
    """
    # 延迟 import 避免循环 (vip_service imports nothing from invite_service, 但保险起见)
    from app.services import vip_service

    settings = get_settings()
    threshold_n = settings.invite_reward_n_users
    vip_days = settings.invite_reward_vip_days

    if threshold_n <= 0 or vip_days <= 0:
        # 配置关闭奖励 — 直接走"未触发"分支, 不查表, 不写 audit
        return InviteRewardResult(
            triggered=False,
            successful_invitee_count=0,
            threshold_n=threshold_n,
            vip_days_granted=vip_days,
        )

    # 1. 数累计成功被邀请数 (active + 未注销)
    # SoftDeleteMixin.deleted_at IS NULL: BE-S5-003 注销后会 set, 这里用 SQL 表达
    count_stmt = (
        select(func.count())
        .select_from(User)
        .where(
            User.invited_by == inviter_user_id,
            User.status == 1,
            User.deleted_at.is_(None),
        )
    )
    invitee_count = int((await session.execute(count_stmt)).scalar_one())

    if invitee_count < threshold_n:
        return InviteRewardResult(
            triggered=False,
            successful_invitee_count=invitee_count,
            threshold_n=threshold_n,
            vip_days_granted=vip_days,
        )

    # 2. 检查 audit 是否已发过同阈值奖励 — INSERT ON CONFLICT DO NOTHING.
    # rowcount=1 说明本次插入成功 (首次达成); rowcount=0 说明 UNIQUE 冲突 (已发过).
    insert_stmt = (
        pg_insert(InviteReward)
        .values(
            inviter_user_id=inviter_user_id,
            threshold_n=threshold_n,
            vip_days_granted=vip_days,
            successful_invitee_count=invitee_count,
        )
        .on_conflict_do_nothing(constraint="uq_invite_rewards_inviter_threshold")
    )
    insert_result = await session.execute(insert_stmt)
    inserted = (getattr(insert_result, "rowcount", 0) or 0) > 0

    if not inserted:
        # 已发过, 静默跳过 (这种情况通常是并发 / 用户从 4 人退到 3 人再涨回 4 人)
        logger.debug(
            f"invite.reward.already_granted inviter={inviter_user_id} "
            f"threshold={threshold_n} count={invitee_count}"
        )
        return InviteRewardResult(
            triggered=False,
            successful_invitee_count=invitee_count,
            threshold_n=threshold_n,
            vip_days_granted=vip_days,
        )

    # 3. 真发奖励: 调 vip_service.extend_membership 延 N 天
    snapshot = await vip_service.extend_membership(
        session,
        user_id=inviter_user_id,
        days=vip_days,
        reason="invite_reward",
    )

    logger.info(
        f"invite.reward.granted inviter={inviter_user_id} threshold={threshold_n} "
        f"invitees={invitee_count} +{vip_days}d new_end_at={snapshot.end_at.isoformat()}"
    )

    return InviteRewardResult(
        triggered=True,
        successful_invitee_count=invitee_count,
        threshold_n=threshold_n,
        vip_days_granted=vip_days,
    )


__all__ = [
    "InviteAlreadyBoundError",
    "InviteBindResult",
    "InviteCodeExhaustedError",
    "InviteCodeExpiredError",
    "InviteCodeInactiveError",
    "InviteCodeNotFoundError",
    "InviteCodeNotPersonalError",
    "InviteError",
    "InviteRewardResult",
    "InviteSelfBindError",
    "apply_invite_reward",
    "bind_invite",
    "register_invite_code_for_user",
]
