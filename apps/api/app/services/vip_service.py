"""VIP 订阅服务层 (BE-S3-009).

5 大职能:
1. ``grant_trial(session, user)``  — 注册时同事务调; 幂等 (同 user_id 已有 membership 则 noop)
2. ``get_active_membership(user_id)`` — 拿当前生效订阅 (status active/trialing + end_at > now)
3. ``is_user_vip(user_id)`` — 配额闸门 ``_resolve_plan`` 用; 走 ``get_active_membership``
4. ``apply_paid_order(session, user, order)`` — BE-S3-010 微信支付回调用; 状态机:
   - 现 status ∈ (trialing, expired, cancelled) → 直接覆盖 start_at=now / end_at=now+plan_duration / status=active
   - 现 status='active' → end_at += plan_duration (堆叠续费)
5. ``expire_overdue_memberships()`` — scheduler 1h 跑; ``UPDATE WHERE status IN
   ('trialing','active') AND end_at < now()`` → status='expired'

试用机制 (spec/06 §2.3)
========================
- 试用 = 一笔 ``vip_orders(plan='trial', amount_cny=0, status='paid',
  payment_channel='internal')`` + 一行 ``vip_memberships(status='trialing',
  plan='trial', start_at=now, end_at=now + 7d, current_order_id=order.id)``.
- 注册成功后调 ``grant_trial``; 幂等 — 已有 membership 不重复授予 (兜底防抖).
- 试用 → 付费时不堆叠剩余天数 (``apply_paid_order`` 走"覆盖"分支), spec/06 §2.3.

状态机
=======
- ``trialing`` → ``active``     (调 ``apply_paid_order``)
- ``trialing`` → ``expired``    (scheduler ``end_at < now``)
- ``active`` → ``active``       (续费堆叠 end_at)
- ``active`` → ``expired``      (scheduler ``end_at < now``)
- ``active`` → ``cancelled``    (Sprint 4+ 退款 / 用户主动取消; 本 PR 不触发)
- ``expired`` → ``active``      (重新付费, 走"覆盖"分支)
- ``cancelled`` → ``active``    (cancel 后又付费, 同样覆盖)

事务边界
=========
- ``grant_trial(session)``: 调用方传 session, 同事务 (与 ``invite_service.register_invite_code_for_user`` 同款); 调用方自己 commit
- ``apply_paid_order(session)``: BE-S3-010 微信回调 service 内部事务, 同上
- ``get_active_membership / is_user_vip / expire_overdue_memberships``: 内部 ``factory()`` 自管 session (无业务事务), 与 ``broker_service`` 同款
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import User, VipMembership, VipOrder

# ─── 常量 ─────────────────────────────────────────────────────────────────

PlanLiteral = Literal["trial", "monthly", "quarterly", "yearly", "lifetime"]
StatusLiteral = Literal["trialing", "active", "expired", "cancelled"]

LIFETIME_END_AT: datetime = datetime(9999, 12, 31, tzinfo=UTC)

# spec/06 §2.2 套餐时长 (天). lifetime 走特殊 end_at 不走 timedelta.
PLAN_DURATION_DAYS: dict[str, int] = {
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365,
}


# ─── 数据传输 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MembershipSnapshot:
    """``get_active_membership`` 返回的轻量快照, 不持有 ORM 引用 (避免跨 session lazy load).

    用于配额闸门 / API 响应 / scheduler 日志 — 任意场景安全传递.
    """

    membership_id: uuid.UUID
    user_id: uuid.UUID
    status: str
    plan: str
    start_at: datetime
    end_at: datetime
    auto_renew: bool
    total_paid_cny: Decimal
    current_order_id: uuid.UUID | None


def _to_snapshot(m: VipMembership) -> MembershipSnapshot:
    return MembershipSnapshot(
        membership_id=m.membership_id,
        user_id=m.user_id,
        status=m.status,
        plan=m.plan,
        start_at=m.start_at,
        end_at=m.end_at,
        auto_renew=m.auto_renew,
        total_paid_cny=m.total_paid_cny,
        current_order_id=m.current_order_id,
    )


# ─── 1. 试用授予 ──────────────────────────────────────────────────────────


def _generate_trial_out_trade_no() -> str:
    """``XGZH-TRIAL-<8 hex>``, 64 字内, 走 ``vip_orders.out_trade_no`` UNIQUE."""
    return f"XGZH-TRIAL-{secrets.token_hex(8)}"


async def grant_trial(
    session: AsyncSession,
    user: User,
    *,
    trial_days: int | None = None,
) -> MembershipSnapshot | None:
    """注册成功后授予 VIP 试用 (幂等).

    流程:
    1. 查现 membership; 若存在直接返回快照 (注册流程兜底, 不重复授予)
    2. 若 ``trial_days <= 0`` (settings 关闭试用), noop, 返 None
    3. 写零元订单 ``vip_orders(plan='trial', amount_cny=0, status='paid',
       payment_channel='internal', paid_at=now)``
    4. 写 membership ``status='trialing', plan='trial', start_at=now,
       end_at=now+trial_days, current_order_id=order.id``
    5. 不 commit (调用方控制事务边界, 与 ``invite_service.register_invite_code_for_user`` 同款)

    Args:
        session: 调用方注入的事务; 本函数仅 ``session.add`` + ``flush``, 不 commit
        user: 必须已 ``flush`` 进 DB 拿到 ``user_id`` (auth_service 流程已确保)
        trial_days: 覆写 settings (单测 / 特殊场景用); 默认 None 走 ``settings.vip_trial_days``

    Returns:
        新建或现有 membership 快照; 关闭试用且 user 无 membership 时返 None.

    设计说明:
    - 幂等检查不走 ``ON CONFLICT (user_id) DO NOTHING`` — 因为我们要返回现有快照,
      还需要拿 ``current_order_id`` 链接订单, ON CONFLICT 不便拿
    - 不 commit: 注册流程是 ``register → grant_trial → register_invite_code → commit``
      一条事务; service 层不应主动 commit, 否则 invite_codes 失败要回滚 membership 也回不掉
    """
    settings = get_settings()
    days = settings.vip_trial_days if trial_days is None else trial_days

    existing = (
        await session.execute(
            select(VipMembership).where(VipMembership.user_id == user.user_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        logger.debug(
            f"vip.grant_trial.skip user_id={user.user_id} existing_status={existing.status}"
        )
        return _to_snapshot(existing)

    if days <= 0:
        logger.info(f"vip.grant_trial.disabled user_id={user.user_id} (vip_trial_days=0)")
        return None

    now = datetime.now(UTC)
    trial_order = VipOrder(
        user_id=user.user_id,
        out_trade_no=_generate_trial_out_trade_no(),
        plan="trial",
        amount_cny=Decimal("0.00"),
        status="paid",
        payment_channel="internal",
        paid_at=now,
    )
    session.add(trial_order)
    await session.flush()  # 拿 order_id

    membership = VipMembership(
        user_id=user.user_id,
        status="trialing",
        plan="trial",
        start_at=now,
        end_at=now + timedelta(days=days),
        auto_renew=False,
        current_order_id=trial_order.order_id,
        total_paid_cny=Decimal("0.00"),
    )
    session.add(membership)
    await session.flush()

    logger.info(
        f"vip.grant_trial.ok user_id={user.user_id} "
        f"membership_id={membership.membership_id} end_at={membership.end_at.isoformat()}"
    )
    return _to_snapshot(membership)


# ─── 2. 当前生效订阅 ──────────────────────────────────────────────────────


async def get_active_membership(user_id: uuid.UUID) -> MembershipSnapshot | None:
    """拿当前生效订阅 (``status IN ('trialing','active') AND end_at > now()``).

    返 None 含义:
    - 用户从未有过 membership
    - 试用 / 订阅已 ``status='expired'`` (scheduler 已处理)
    - 试用 / 订阅 ``end_at <= now`` 但 scheduler 还没跑 (实时判断, 用户体验更准)
    - 主动 ``cancelled``

    走 ``ix_vip_memberships_status_end_at`` 索引 (status, end_at), 配合 user_id
    UNIQUE 命中点查 < 1ms.
    """
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        m = (
            await session.execute(
                select(VipMembership).where(
                    VipMembership.user_id == user_id,
                    VipMembership.status.in_(("trialing", "active")),
                    VipMembership.end_at > now,
                )
            )
        ).scalar_one_or_none()
        return _to_snapshot(m) if m is not None else None


async def get_any_membership(user_id: uuid.UUID) -> MembershipSnapshot | None:
    """拿用户任意状态的 membership (含 expired / cancelled), 用于 ``/vip/me`` 历史展示.

    单点查 ``user_id`` UNIQUE 命中, < 1ms. 区别于 ``get_active_membership``: 不过滤
    status / end_at, 让前端能展示"上次订阅 2024-01-15 已到期" 这类历史信息.
    """
    factory = get_session_factory()
    async with factory() as session:
        m = (
            await session.execute(
                select(VipMembership).where(VipMembership.user_id == user_id)
            )
        ).scalar_one_or_none()
        return _to_snapshot(m) if m is not None else None


async def is_user_vip(user_id: uuid.UUID) -> bool:
    """``_resolve_plan`` 用的轻量布尔判断.

    与 ``get_active_membership`` 等价, 但端到端 SELECT 1 + 索引命中, 不 build snapshot.
    """
    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as session:
        # 走 EXISTS 单 SELECT, 不取数据
        from sqlalchemy import literal

        stmt = select(literal(1)).where(
            VipMembership.user_id == user_id,
            VipMembership.status.in_(("trialing", "active")),
            VipMembership.end_at > now,
        )
        return (await session.execute(stmt)).scalar_one_or_none() is not None


# ─── 3. 续费 / 付费 → 状态机 ───────────────────────────────────────────────


async def apply_paid_order(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    order: VipOrder,
) -> MembershipSnapshot:
    """订单 ``status='paid'`` 时驱动 membership 状态机 (BE-S3-010 微信回调用).

    Args:
        session: 调用方事务 (与 grant_trial 同款, 不 commit)
        user_id: 订单归属 (与 ``order.user_id`` 一致, 显式传防误用)
        order: 已 ``flush`` 进 DB 且 ``status='paid'`` 的订单 (本函数不验签 / 不改订单状态)

    流程:
    1. 取 membership (1 行 UNIQUE 拿)
       - 不存在 (异常: 注册时 grant_trial 失败) → 直接新建一行 ``status='active'``
       - 存在 + 现 status ∈ (trialing/expired/cancelled) → 覆盖 start_at=now / end_at=now+duration / status=active
       - 存在 + 现 status=active → end_at += duration (堆叠续费; 起算点为现 end_at, 不是 now)
    2. ``current_order_id = order.order_id``
    3. ``total_paid_cny += order.amount_cny`` (累计支付额, 财务对账用; lifetime / quarterly 同样累加)
    4. plan 字段总是更新为本次订单的 plan (不混合; 月转年时 plan='yearly')

    返回更新后快照. ``order.amount_cny`` 必须 ≥ 0; 不验签 (BE-S3-010 上游已验).
    """
    if order.status != "paid":
        raise ValueError(f"apply_paid_order requires order.status='paid', got {order.status!r}")
    plan = order.plan
    if plan == "trial":
        raise ValueError(
            "apply_paid_order 不接受 trial plan (用 grant_trial 走零元订单注册路径)"
        )

    now = datetime.now(UTC)

    membership = (
        await session.execute(
            select(VipMembership).where(VipMembership.user_id == user_id)
        )
    ).scalar_one_or_none()

    if membership is None:
        # 异常路径: 注册时 grant_trial 失败 / 老用户 (PR 上线前注册) 直接付费
        membership = VipMembership(
            user_id=user_id,
            status="active",
            plan=plan,
            start_at=now,
            end_at=_compute_end_at(plan, base=now),
            auto_renew=False,
            current_order_id=order.order_id,
            total_paid_cny=order.amount_cny,
        )
        session.add(membership)
        await session.flush()
        logger.info(
            f"vip.apply_paid.new_membership user_id={user_id} plan={plan} "
            f"order_id={order.order_id} end_at={membership.end_at.isoformat()}"
        )
        return _to_snapshot(membership)

    if membership.status == "active":
        base = max(membership.end_at, now)
        membership.end_at = _compute_end_at(plan, base=base)
        logger.info(
            f"vip.apply_paid.stack user_id={user_id} prev_end={membership.end_at.isoformat()} "
            f"plan={plan}"
        )
    else:
        # trialing / expired / cancelled → 覆盖
        membership.start_at = now
        membership.end_at = _compute_end_at(plan, base=now)
        membership.status = "active"
        logger.info(
            f"vip.apply_paid.replace user_id={user_id} prev_status={membership.status!r}→active "
            f"plan={plan} end_at={membership.end_at.isoformat()}"
        )

    membership.plan = plan
    membership.current_order_id = order.order_id
    membership.total_paid_cny = membership.total_paid_cny + order.amount_cny
    await session.flush()
    return _to_snapshot(membership)


# ─── 3.5 reward 延期 (BE-S5-005 邀请有礼用) ─────────────────────────────


async def extend_membership(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    days: int,
    reason: str = "invite_reward",
) -> MembershipSnapshot:
    """**纯延期**: 把 user 的 VIP end_at 往后推 ``days`` 天.

    与 ``apply_paid_order`` 的区别: 这个不创建 VipOrder, 不改 ``current_order_id``,
    不动 ``total_paid_cny`` (没真支付). 只动 ``end_at`` + 必要时改 ``status``.

    状态机:
    - 无 membership (老用户 / grant_trial 失败) → 新建 ``status='active', plan='trial',
      start_at=now, end_at=now+days``; plan='trial' 是因为 ``vip_memberships.plan`` 不接
      "reward" 字面值, 用 'trial' 表示"非付费来源" (与零元订单同款)
    - status ∈ (trialing, active) AND end_at > now → 直接 ``end_at += days``, status 不动
    - status ∈ (expired, cancelled) OR end_at ≤ now → 重置: start_at=now, end_at=now+days,
      status='active' (从过期 / 取消重新激活, 与 ``apply_paid_order`` 的覆盖分支一致)

    Args:
        session: 调用方传, 同事务 (与 grant_trial / apply_paid_order 同款)
        user_id: 必须存在的用户; 不存在 → IntegrityError 由调用方处理
        days: ≥ 0; 0 视为 noop 但仍返当前 snapshot
        reason: 仅 logger 用, 审计 trail (如 "invite_reward" / "manual_grant")

    Returns:
        延期后 snapshot.
    """
    if days < 0:
        raise ValueError(f"extend_membership days must be >= 0, got {days}")

    now = datetime.now(UTC)
    membership = (
        await session.execute(
            select(VipMembership).where(VipMembership.user_id == user_id)
        )
    ).scalar_one_or_none()

    if membership is None:
        membership = VipMembership(
            user_id=user_id,
            status="active",
            plan="trial",  # 非付费来源, 与零元订单一致
            start_at=now,
            end_at=now + timedelta(days=days),
            auto_renew=False,
            current_order_id=None,
            total_paid_cny=Decimal("0.00"),
        )
        session.add(membership)
        await session.flush()
        logger.info(
            f"vip.extend.new user_id={user_id} days={days} reason={reason} "
            f"end_at={membership.end_at.isoformat()}"
        )
        return _to_snapshot(membership)

    if days == 0:
        return _to_snapshot(membership)

    if membership.status in ("trialing", "active") and membership.end_at > now:
        # 仍生效 → 直接延期
        membership.end_at = membership.end_at + timedelta(days=days)
        logger.info(
            f"vip.extend.stack user_id={user_id} +{days}d reason={reason} "
            f"new_end_at={membership.end_at.isoformat()}"
        )
    else:
        # 已过期 / cancelled → 重置激活
        membership.start_at = now
        membership.end_at = now + timedelta(days=days)
        prev_status = membership.status
        membership.status = "active"
        logger.info(
            f"vip.extend.reactivate user_id={user_id} prev_status={prev_status!r}→active "
            f"days={days} reason={reason} end_at={membership.end_at.isoformat()}"
        )

    await session.flush()
    return _to_snapshot(membership)


def _compute_end_at(plan: str, *, base: datetime) -> datetime:
    """根据 plan + base 起算点算 end_at.

    lifetime 设 9999-12-31 (绝对值, 不走 timedelta) — 与 ORM model 注释一致,
    避免业务层任何 ``end_at IS NULL`` 分支.
    """
    if plan == "lifetime":
        return LIFETIME_END_AT
    days = PLAN_DURATION_DAYS.get(plan)
    if days is None:
        raise ValueError(f"unknown plan {plan!r}; expect monthly/quarterly/yearly/lifetime")
    return base + timedelta(days=days)


# ─── 4. 过期清扫 (scheduler) ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExpireResult:
    """``expire_overdue_memberships`` 返回结构, 给 scheduler 日志 + 测试断言用."""

    expired: int
    """本次扫描标记为 expired 的行数 (status active/trialing → expired)."""


async def expire_overdue_memberships() -> ExpireResult:
    """扫描所有 ``status IN ('trialing','active') AND end_at < now`` 的行, 改 ``expired``.

    走 ``ix_vip_memberships_status_end_at`` 索引 (status, end_at) — 范围扫描;
    高水位用户量 (10K+ active membership) 下单次 < 100ms.

    设计:
    - 单条 UPDATE WHERE 完成, 不分批; 量大时 (> 10K 行 / 次) 才考虑加 LIMIT 分页,
      Sprint 3 / 4 不会到这量级
    - 不更新 ``updated_at`` 走 onupdate=server_default (TimestampMixin), 自动触发
    - **不抛**: scheduler 单 job 失败重试机制由 APScheduler 自己管 (misfire_grace_time=300)
    """
    now = datetime.now(UTC)
    factory = get_session_factory()
    async with factory() as session, session.begin():
        result = await session.execute(
            update(VipMembership)
            .where(
                VipMembership.status.in_(("trialing", "active")),
                VipMembership.end_at < now,
            )
            .values(status="expired")
            .execution_options(synchronize_session=False)
        )
        # SQLAlchemy 2.x async UPDATE 返 ``CursorResult``, 但泛型基类签名上没暴露
        # ``rowcount``; ``getattr`` 兜底比 isinstance 检查更稳 (避免 ChunkedIteratorResult
        # 之类的子类绕过 isinstance).
        affected = int(getattr(result, "rowcount", 0) or 0)

    if affected:
        logger.info(f"vip.expire_overdue.swept count={affected} now={now.isoformat()}")
    else:
        logger.debug(f"vip.expire_overdue.empty now={now.isoformat()}")
    return ExpireResult(expired=affected)


async def run_expire_overdue_job() -> None:
    """APScheduler 包装 (与 ``run_recluster_job`` / ``run_sentiment_tag_job`` 同款).

    scheduler 失败兜底: 任何异常都 ``logger.exception`` 不抛, 让 scheduler 不会
    把 job 标 misfire (我们希望它继续按 cron 跑而不是被踢掉).
    """
    try:
        await expire_overdue_memberships()
    except Exception as e:  # noqa: BLE001
        logger.exception(f"vip.expire_overdue.job_failed: {e}")


# ─── 5. 订单查询 (api/v1/vip 用) ───────────────────────────────────────────


async def list_user_orders(
    user_id: uuid.UUID, *, limit: int = 20
) -> list[VipOrder]:
    """用户订单历史, 默认最近 20 条.

    走 ``ix_vip_orders_user_created`` 索引 (user_id, created_at DESC), < 10ms.
    返 ORM 实例列表 (路由层 model_validate 转 schema).
    """
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(VipOrder)
                .where(VipOrder.user_id == user_id)
                .order_by(VipOrder.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        return list(rows)


__all__ = [
    "ExpireResult",
    "MembershipSnapshot",
    "PLAN_DURATION_DAYS",
    "apply_paid_order",
    "expire_overdue_memberships",
    "extend_membership",
    "get_active_membership",
    "get_any_membership",
    "grant_trial",
    "is_user_vip",
    "list_user_orders",
    "run_expire_overdue_job",
]
