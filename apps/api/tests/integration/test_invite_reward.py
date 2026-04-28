"""BE-S5-005 邀请有礼 trigger 集成测.

覆盖 (spec/12 §AC):
1.  N=3 时第 1/2 人 bind 不触发, 第 3 人 bind 触发 +7d
2.  阈值已发过 → 第 4 人 bind 不再触发 (audit UNIQUE)
3.  禁用用户 (status=0) 不算入邀请数
4.  软删用户 (deleted_at IS NOT NULL) 不算入邀请数
5.  inviter 现有 trial 期 → end_at += 7d 堆叠 (不重置 start)
6.  inviter 已过期 → reactivate (status='expired' → 'active', start_at=now, end_at=now+7d)
7.  inviter 无 membership → 新建 (status='active', plan='trial', end_at=now+7d)
8.  关闭奖励 (invite_reward_n_users=0) → 永远不触发
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models import (
    InviteReward,
    User,
    VipMembership,
    VipOrder,
)
from app.services import invite_service

pytestmark = [pytest.mark.asyncio, pytest.mark.db]


# ─── 工具: 直接造邀请关系数据 (绕过 OTP / register 流程, 让用例聚焦 reward 逻辑) ──


async def _seed_inviter(
    session: AsyncSession, *, with_trial_days: int | None = 7
) -> User:
    """造一个 inviter 用户. ``with_trial_days``: 0 / None 不发 trial, > 0 发对应天数 trial."""
    code = f"TST{uuid.uuid4().hex[:5].upper()}"
    user = User(
        phone=f"+8613{uuid.uuid4().int % 10**9:09d}",
        invite_code=code,
        status=1,
    )
    session.add(user)
    await session.flush()

    # 镜像到 invite_codes 表 (复用 invite_service 既有 helper)
    await invite_service.register_invite_code_for_user(session, user)

    if with_trial_days is not None and with_trial_days > 0:
        now = datetime.now(UTC)
        order = VipOrder(
            user_id=user.user_id,
            out_trade_no=f"XGZH-TRIAL-{uuid.uuid4().hex[:8]}",
            plan="trial",
            amount_cny=Decimal("0.00"),
            status="paid",
            payment_channel="internal",
            paid_at=now,
        )
        session.add(order)
        await session.flush()
        membership = VipMembership(
            user_id=user.user_id,
            status="trialing",
            plan="trial",
            start_at=now,
            end_at=now + timedelta(days=with_trial_days),
            auto_renew=False,
            current_order_id=order.order_id,
            total_paid_cny=Decimal("0.00"),
        )
        session.add(membership)
        await session.flush()
    return user


async def _seed_invitee(
    session: AsyncSession,
    *,
    inviter_id: uuid.UUID,
    status: int = 1,
    deleted: bool = False,
) -> User:
    """造一个被邀请人, 直接落 ``invited_by``. 跳过 invite_codes / VIP trial."""
    user = User(
        phone=f"+8615{uuid.uuid4().int % 10**9:09d}",
        invite_code=f"INV{uuid.uuid4().hex[:5].upper()}",
        invited_by=inviter_id,
        status=status,
    )
    session.add(user)
    await session.flush()
    await invite_service.register_invite_code_for_user(session, user)
    if deleted:
        user.deleted_at = datetime.now(UTC)
        await session.flush()
    return user


# ─── 1. happy: 阈值首次达成触发 ────────────────────────────────────────


async def test_third_invitee_triggers_reward(
    client: httpx.AsyncClient,  # noqa: ARG001 - 仅借 fixture chain 起 schema + redis
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """N=3: 前 2 人不触发, 第 3 人 apply_invite_reward 直接拉到 triggered=True."""
    async with session_factory() as session:
        inviter = await _seed_inviter(session, with_trial_days=7)
        await _seed_invitee(session, inviter_id=inviter.user_id)
        await _seed_invitee(session, inviter_id=inviter.user_id)
        await session.commit()

    async with session_factory() as session:
        r1 = await invite_service.apply_invite_reward(
            session, inviter_user_id=inviter.user_id
        )
        await session.commit()
    assert r1.triggered is False
    assert r1.successful_invitee_count == 2
    assert r1.threshold_n == 3
    assert r1.vip_days_granted == 7

    async with session_factory() as session:
        await _seed_invitee(session, inviter_id=inviter.user_id)
        await session.commit()

    async with session_factory() as session:
        r2 = await invite_service.apply_invite_reward(
            session, inviter_user_id=inviter.user_id
        )
        await session.commit()
    assert r2.triggered is True
    assert r2.successful_invitee_count == 3

    # audit 表写一行 + vip end_at 真延期
    async with session_factory() as session:
        rewards = (
            (await session.execute(select(InviteReward).where(InviteReward.inviter_user_id == inviter.user_id)))
            .scalars()
            .all()
        )
        assert len(rewards) == 1
        assert rewards[0].threshold_n == 3
        assert rewards[0].vip_days_granted == 7
        assert rewards[0].successful_invitee_count == 3

        membership = (
            await session.execute(
                select(VipMembership).where(VipMembership.user_id == inviter.user_id)
            )
        ).scalar_one()
        # trial 7 天 + reward 7 天 ≈ 14 天 (允许 5s 误差)
        expected_end = datetime.now(UTC) + timedelta(days=14)
        assert abs((membership.end_at - expected_end).total_seconds()) < 60


# ─── 2. 单阈值幂等: 第 4/5 人不重复触发 ─────────────────────────────────


async def test_threshold_idempotent_no_double_grant(
    client: httpx.AsyncClient,  # noqa: ARG001
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """达到阈值后再加被邀请人, 同 threshold_n 不再触发, audit 仍 1 行."""
    async with session_factory() as session:
        inviter = await _seed_inviter(session, with_trial_days=7)
        for _ in range(3):
            await _seed_invitee(session, inviter_id=inviter.user_id)
        await session.commit()

    async with session_factory() as session:
        r1 = await invite_service.apply_invite_reward(
            session, inviter_user_id=inviter.user_id
        )
        await session.commit()
    assert r1.triggered is True
    end_after_first = await _read_end_at(session_factory, inviter.user_id)

    # 再加一个被邀请人 + 再调一次 → 不触发, end_at 不变
    async with session_factory() as session:
        await _seed_invitee(session, inviter_id=inviter.user_id)
        await session.commit()

    async with session_factory() as session:
        r2 = await invite_service.apply_invite_reward(
            session, inviter_user_id=inviter.user_id
        )
        await session.commit()
    assert r2.triggered is False
    assert r2.successful_invitee_count == 4

    end_after_second = await _read_end_at(session_factory, inviter.user_id)
    assert end_after_first == end_after_second

    async with session_factory() as session:
        rewards = (
            await session.execute(
                select(InviteReward).where(InviteReward.inviter_user_id == inviter.user_id)
            )
        ).scalars().all()
        assert len(rewards) == 1


async def _read_end_at(
    session_factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID
) -> datetime:
    async with session_factory() as session:
        m = (
            await session.execute(
                select(VipMembership).where(VipMembership.user_id == user_id)
            )
        ).scalar_one()
        return m.end_at


# ─── 3. 防刷: 禁用 / 软删用户不计入 ────────────────────────────────────


async def test_disabled_invitee_not_counted(
    client: httpx.AsyncClient,  # noqa: ARG001
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """status=0 (禁用) 的被邀请人不算入数. 即使有 3 个 record, 实际只算 active 的."""
    async with session_factory() as session:
        inviter = await _seed_inviter(session, with_trial_days=7)
        await _seed_invitee(session, inviter_id=inviter.user_id, status=1)
        await _seed_invitee(session, inviter_id=inviter.user_id, status=0)  # 禁用
        await _seed_invitee(session, inviter_id=inviter.user_id, status=0)  # 禁用
        await session.commit()

    async with session_factory() as session:
        r = await invite_service.apply_invite_reward(
            session, inviter_user_id=inviter.user_id
        )
        await session.commit()
    assert r.triggered is False  # 只 1 个 active, 没达 3
    assert r.successful_invitee_count == 1


async def test_softdeleted_invitee_not_counted(
    client: httpx.AsyncClient,  # noqa: ARG001
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """deleted_at IS NOT NULL 的被邀请人不算入 (BE-S5-003 注销后兼容)."""
    async with session_factory() as session:
        inviter = await _seed_inviter(session, with_trial_days=7)
        await _seed_invitee(session, inviter_id=inviter.user_id)
        await _seed_invitee(session, inviter_id=inviter.user_id, deleted=True)
        await _seed_invitee(session, inviter_id=inviter.user_id, deleted=True)
        await session.commit()

    async with session_factory() as session:
        r = await invite_service.apply_invite_reward(
            session, inviter_user_id=inviter.user_id
        )
        await session.commit()
    assert r.triggered is False
    assert r.successful_invitee_count == 1


# ─── 4. extend_membership 不同状态分支 ─────────────────────────────────


async def test_extend_membership_when_no_membership_creates_new(
    client: httpx.AsyncClient,  # noqa: ARG001
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """老用户 (没 trial) 触发奖励 → 新建 membership status='active'."""
    async with session_factory() as session:
        inviter = await _seed_inviter(session, with_trial_days=0)  # 不发 trial
        for _ in range(3):
            await _seed_invitee(session, inviter_id=inviter.user_id)
        await session.commit()

    async with session_factory() as session:
        r = await invite_service.apply_invite_reward(
            session, inviter_user_id=inviter.user_id
        )
        await session.commit()
    assert r.triggered is True

    async with session_factory() as session:
        m = (
            await session.execute(
                select(VipMembership).where(VipMembership.user_id == inviter.user_id)
            )
        ).scalar_one()
        assert m.status == "active"
        assert m.plan == "trial"  # 非付费来源, 沿用 trial 字面值
        assert m.current_order_id is None
        # end_at ≈ now + 7d
        expected = datetime.now(UTC) + timedelta(days=7)
        assert abs((m.end_at - expected).total_seconds()) < 60


async def test_extend_membership_when_expired_reactivates(
    client: httpx.AsyncClient,  # noqa: ARG001
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """已过期 membership → reactivate (status: expired → active, end_at: now+7d)."""
    async with session_factory() as session:
        inviter = await _seed_inviter(session, with_trial_days=0)
        # 手工塞一个 expired membership
        past = datetime.now(UTC) - timedelta(days=10)
        order = VipOrder(
            user_id=inviter.user_id,
            out_trade_no=f"XGZH-TRIAL-{uuid.uuid4().hex[:8]}",
            plan="trial",
            amount_cny=Decimal("0.00"),
            status="paid",
            payment_channel="internal",
            paid_at=past,
        )
        session.add(order)
        await session.flush()
        membership = VipMembership(
            user_id=inviter.user_id,
            status="expired",
            plan="trial",
            start_at=past - timedelta(days=7),
            end_at=past,
            auto_renew=False,
            current_order_id=order.order_id,
            total_paid_cny=Decimal("0.00"),
        )
        session.add(membership)
        await session.flush()
        for _ in range(3):
            await _seed_invitee(session, inviter_id=inviter.user_id)
        await session.commit()

    async with session_factory() as session:
        r = await invite_service.apply_invite_reward(
            session, inviter_user_id=inviter.user_id
        )
        await session.commit()
    assert r.triggered is True

    async with session_factory() as session:
        m = (
            await session.execute(
                select(VipMembership).where(VipMembership.user_id == inviter.user_id)
            )
        ).scalar_one()
        assert m.status == "active"
        # start_at reset 到现在 (允许 5s 误差)
        assert abs((m.start_at - datetime.now(UTC)).total_seconds()) < 60


# ─── 5. 关闭奖励 ──────────────────────────────────────────────────────


async def test_disabled_reward_never_triggers(
    client: httpx.AsyncClient,  # noqa: ARG001
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``invite_reward_n_users=0`` 关闭奖励, 即便 100 人达成也不触发."""
    monkeypatch.setenv("INVITE_REWARD_N_USERS", "0")
    get_settings.cache_clear()

    try:
        async with session_factory() as session:
            inviter = await _seed_inviter(session, with_trial_days=7)
            for _ in range(5):
                await _seed_invitee(session, inviter_id=inviter.user_id)
            await session.commit()

        async with session_factory() as session:
            r = await invite_service.apply_invite_reward(
                session, inviter_user_id=inviter.user_id
            )
            await session.commit()
        assert r.triggered is False
        assert r.threshold_n == 0

        # audit 表无任何行
        async with session_factory() as session:
            rewards = (
                await session.execute(
                    select(InviteReward).where(
                        InviteReward.inviter_user_id == inviter.user_id
                    )
                )
            ).scalars().all()
            assert len(rewards) == 0
    finally:
        get_settings.cache_clear()


# ─── 6. 端到端 bind_invite → reward 自动触发 ──────────────────────────


async def test_bind_invite_route_triggers_reward_on_third(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """走 ``POST /invite/bind`` 真走 HTTP, 第 3 个被邀请人 bind 后 inviter 拿到 +7d.

    用 invite_service.bind_invite 内部调用而非 HTTP 是因为登录态需要 OTP / 微信流程,
    本用例聚焦 bind → reward 的 service 层串联. HTTP 路径覆盖在 ``test_invite.py``
    bind_invite 既有用例 (那些只断 200, 不断 reward).
    """
    async with session_factory() as session:
        inviter = await _seed_inviter(session, with_trial_days=7)
        # 拿 inviter 的 invite_code 字符串 (db 已落)
        inviter_code = inviter.invite_code
        await session.commit()

    # 预先造 2 个被邀请人, 直接 set invited_by (绕开 bind) 模拟之前已经被邀请过
    async with session_factory() as session:
        await _seed_invitee(session, inviter_id=inviter.user_id)
        await _seed_invitee(session, inviter_id=inviter.user_id)
        await session.commit()

    # 第 3 个被邀请人走真 bind_invite 路径
    async with session_factory() as session:
        third = User(
            phone="+8615900000003",
            invite_code=f"BND{uuid.uuid4().hex[:5].upper()}",
            status=1,
        )
        session.add(third)
        await session.flush()
        await invite_service.register_invite_code_for_user(session, third)
        await session.commit()

    async with session_factory() as session:
        third = (
            await session.execute(select(User).where(User.phone == "+8615900000003"))
        ).scalar_one()
        result = await invite_service.bind_invite(
            session, current_user=third, code=inviter_code
        )
        # bind_invite 内部 commit
    assert result.referrer_user_id == inviter.user_id

    # bind_invite 末尾的 reward trigger 异步走了独立 session — 同步检查 audit / vip
    async with session_factory() as session:
        rewards = (
            await session.execute(
                select(InviteReward).where(
                    InviteReward.inviter_user_id == inviter.user_id
                )
            )
        ).scalars().all()
        assert len(rewards) == 1
        assert rewards[0].threshold_n == 3
        assert rewards[0].successful_invitee_count == 3

        m = (
            await session.execute(
                select(VipMembership).where(VipMembership.user_id == inviter.user_id)
            )
        ).scalar_one()
        # 7d trial + 7d reward = 14d (允许误差)
        expected = datetime.now(UTC) + timedelta(days=14)
        assert abs((m.end_at - expected).total_seconds()) < 60


# ─── 7. 防刷: 自禁 (用户拿自己 invite_code 绑) bind_invite 已挡, 不触发 reward ──


async def test_self_bind_does_not_trigger_reward(
    client: httpx.AsyncClient,  # noqa: ARG001
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """``InviteSelfBindError`` raise 在 reward trigger 之前, audit 应零行."""
    async with session_factory() as session:
        inviter = await _seed_inviter(session, with_trial_days=7)
        await session.commit()

    async with session_factory() as session:
        inviter_db = (
            await session.execute(select(User).where(User.user_id == inviter.user_id))
        ).scalar_one()
        with pytest.raises(invite_service.InviteSelfBindError):
            await invite_service.bind_invite(
                session, current_user=inviter_db, code=inviter_db.invite_code
            )

    async with session_factory() as session:
        rewards = (
            await session.execute(
                select(InviteReward).where(
                    InviteReward.inviter_user_id == inviter.user_id
                )
            )
        ).scalars().all()
        assert len(rewards) == 0
