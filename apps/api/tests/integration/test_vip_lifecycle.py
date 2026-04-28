"""BE-S3-009 VIP 订阅 / 试用 / 状态机 / 配额闸门 端到端集成测.

覆盖 (spec/10 §BE-S3-009 AC + 防御性反向用例):

注册 → 试用授予:
1.  手机号注册 ``/auth/login/phone`` → 自动建 vip_memberships.status='trialing'
    + vip_orders(plan='trial', amount_cny=0, payment_channel='internal')
2.  幂等: 同 user 二次走 grant_trial → 不重复授予 (membership 仍 1 行)
3.  ``vip_trial_days=0`` → 不授予, 不报错 (用户走 FREE)

GET /vip/me + /vip/orders:
4.  /vip/me 401 unauthenticated
5.  /vip/me 已注册用户 → has_active=True / status='trialing' / days_remaining ~7
6.  /vip/orders 列表 → 1 笔零元 internal 订单, raw_callback 字段不暴露

状态机 (apply_paid_order):
7.  trialing → active 覆盖: end_at = now + 30d (不堆叠剩余试用)
8.  active 续费堆叠: 现 end_at + 30d (从现 end_at 起算, 不从 now)
9.  expired → active 覆盖: 重新激活, start_at=now / end_at=now+30d
10. lifetime: end_at=9999-12-31

Scheduler expire job:
11. 试用 end_at < now → expire_overdue_memberships() 标记 expired (1 行)
12. /vip/me 在 expire 后返 has_active=False + 历史信息

配额闸门 _resolve_plan 接真表:
13. trialing user → resolve_plan_with_membership = VIP (端到端 SSE 不挡)
14. expired user (无白名单) → FREE
15. 白名单仍兜底: 即便无 membership 行, vip_user_id_whitelist 命中也走 VIP
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import User, VipMembership, VipOrder
from app.services import otp_service, vip_service
from app.services.agent.quota import (
    QuotaPlan,
    _resolve_plan_with_membership,
)

pytestmark = pytest.mark.db


# ─── helpers ───────────────────────────────────────────────────────────────


async def _register_user_via_otp(
    client: httpx.AsyncClient,
    *,
    phone: str = "+8613800138777",
    code: str = "654321",
) -> tuple[str, str]:
    """走完整 OTP → /auth/login/phone, 返 (user_id, access_token)."""
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    resp = await client.post(
        "/api/v1/auth/login/phone", json={"phone": phone, "code": code}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_new_user"] is True, "首次注册必须 is_new_user=True"
    return body["user"]["user_id"], body["tokens"]["access_token"]


async def _seed_user(
    factory: async_sessionmaker[AsyncSession],
    *,
    phone_suffix: str,
) -> uuid.UUID:
    async with factory() as s:
        u = User(
            phone=f"+861380019{phone_suffix}",
            invite_code=f"VIP{phone_suffix}",
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        return u.user_id


async def _membership_count(
    factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID
) -> int:
    async with factory() as s:
        rows = (
            await s.execute(
                select(VipMembership).where(VipMembership.user_id == user_id)
            )
        ).scalars().all()
        return len(rows)


async def _order_count(
    factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID
) -> int:
    async with factory() as s:
        rows = (
            await s.execute(
                select(VipOrder).where(VipOrder.user_id == user_id)
            )
        ).scalars().all()
        return len(rows)


# ─── 1. 注册自动授予试用 ────────────────────────────────────────────────


async def test_phone_register_grants_7d_trial(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id_str, _token = await _register_user_via_otp(client)
    user_id = uuid.UUID(user_id_str)

    # membership trialing 1 行 + zero-amount internal order 1 笔
    async with session_factory() as s:
        m = (
            await s.execute(
                select(VipMembership).where(VipMembership.user_id == user_id)
            )
        ).scalar_one()
        assert m.status == "trialing"
        assert m.plan == "trial"
        assert m.total_paid_cny == Decimal("0.00")
        delta = m.end_at - m.start_at
        # 7 天 ± 1s 容忍 (不同 datetime.now() 调用)
        assert timedelta(days=7) - timedelta(seconds=2) <= delta <= timedelta(days=7) + timedelta(seconds=2)

        order = (
            await s.execute(
                select(VipOrder).where(VipOrder.user_id == user_id)
            )
        ).scalar_one()
        assert order.plan == "trial"
        assert order.amount_cny == Decimal("0.00")
        assert order.status == "paid"
        assert order.payment_channel == "internal"
        assert order.out_trade_no.startswith("XGZH-TRIAL-")
        assert m.current_order_id == order.order_id


# ─── 2. 幂等: 二次 grant_trial 不重复授予 ──────────────────────────────


async def test_grant_trial_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    user_id = await _seed_user(session_factory, phone_suffix="0002")

    # 第一次
    async with session_factory() as s:
        u = (
            await s.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()
        snap1 = await vip_service.grant_trial(s, u)
        await s.commit()
    assert snap1 is not None
    assert snap1.status == "trialing"

    # 第二次 — 应直接返现快照, 不新建
    async with session_factory() as s:
        u = (
            await s.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()
        snap2 = await vip_service.grant_trial(s, u)
        await s.commit()
    assert snap2 is not None
    assert snap2.membership_id == snap1.membership_id

    assert await _membership_count(session_factory, user_id) == 1
    assert await _order_count(session_factory, user_id) == 1


# ─── 3. vip_trial_days=0 → 不授予, 不报错 ───────────────────────────────


async def test_grant_trial_disabled_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    user_id = await _seed_user(session_factory, phone_suffix="0003")
    async with session_factory() as s:
        u = (
            await s.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()
        snap = await vip_service.grant_trial(s, u, trial_days=0)
        await s.commit()
    assert snap is None
    assert await _membership_count(session_factory, user_id) == 0
    assert await _order_count(session_factory, user_id) == 0


# ─── 4. /vip/me 401 ────────────────────────────────────────────────────


async def test_vip_me_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/vip/me")
    assert resp.status_code == 401


# ─── 5. /vip/me 注册后返试用信息 ───────────────────────────────────────


async def test_vip_me_returns_trial_membership(
    client: httpx.AsyncClient,
) -> None:
    _user_id, token = await _register_user_via_otp(
        client, phone=f"+8613800138{uuid.uuid4().int % 1000:03d}"
    )
    resp = await client.get(
        "/api/v1/vip/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_active"] is True
    assert body["status"] == "trialing"
    assert body["plan"] == "trial"
    assert body["days_remaining"] in (6, 7)  # 7 天 - 计算时已减秒, 容忍 6
    # Decimal NUMERIC(10,2) 序列化保留 2 位小数
    assert body["total_paid_cny"] == "0.00"


# ─── 6. /vip/orders 列表 (raw_callback 不暴露) ──────────────────────────


async def test_vip_orders_lists_trial_order(client: httpx.AsyncClient) -> None:
    _, token = await _register_user_via_otp(
        client, phone=f"+8613800138{uuid.uuid4().int % 1000:03d}"
    )
    resp = await client.get(
        "/api/v1/vip/orders", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["plan"] == "trial"
    assert item["amount_cny"] == "0.00"
    assert item["status"] == "paid"
    assert item["payment_channel"] == "internal"
    # raw_callback / extra 字段不暴露 (Pydantic extra='forbid' + 我们没列出来)
    assert "raw_callback" not in item


# ─── 7. trialing → active 覆盖 (不堆叠剩余试用) ─────────────────────────


async def test_apply_paid_order_replace_trialing(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    user_id = await _seed_user(session_factory, phone_suffix="0007")
    # 先 grant_trial
    async with session_factory() as s:
        u = (
            await s.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()
        await vip_service.grant_trial(s, u)
        await s.commit()

    # 模拟微信支付成功: 写 paid 订单 → apply_paid_order
    async with session_factory() as s:
        order = VipOrder(
            user_id=user_id,
            out_trade_no=f"XGZH-{uuid.uuid4().hex[:12]}",
            plan="monthly",
            amount_cny=Decimal("39.00"),
            status="paid",
            payment_channel="wechat_mp",
            paid_at=datetime.now(UTC),
        )
        s.add(order)
        await s.flush()
        snap = await vip_service.apply_paid_order(s, user_id=user_id, order=order)
        await s.commit()

    assert snap.status == "active"
    assert snap.plan == "monthly"
    # end_at = now + 30d (不是 trial 7d + monthly 30d)
    expected_end = datetime.now(UTC) + timedelta(days=30)
    assert abs((snap.end_at - expected_end).total_seconds()) < 5
    assert snap.total_paid_cny == Decimal("39.00")


# ─── 8. active 续费堆叠 ────────────────────────────────────────────────


async def test_apply_paid_order_stacks_active(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    user_id = await _seed_user(session_factory, phone_suffix="0008")
    # 先 trialing → active
    async with session_factory() as s:
        u = (
            await s.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()
        await vip_service.grant_trial(s, u)
        order1 = VipOrder(
            user_id=user_id,
            out_trade_no=f"XGZH-{uuid.uuid4().hex[:12]}",
            plan="monthly",
            amount_cny=Decimal("39.00"),
            status="paid",
            payment_channel="wechat_mp",
            paid_at=datetime.now(UTC),
        )
        s.add(order1)
        await s.flush()
        snap1 = await vip_service.apply_paid_order(s, user_id=user_id, order=order1)
        await s.commit()
    end_after_first = snap1.end_at

    # 第二笔 monthly: 应该 += 30d 不归零
    async with session_factory() as s:
        order2 = VipOrder(
            user_id=user_id,
            out_trade_no=f"XGZH-{uuid.uuid4().hex[:12]}",
            plan="monthly",
            amount_cny=Decimal("39.00"),
            status="paid",
            payment_channel="wechat_mp",
            paid_at=datetime.now(UTC),
        )
        s.add(order2)
        await s.flush()
        snap2 = await vip_service.apply_paid_order(s, user_id=user_id, order=order2)
        await s.commit()

    delta = snap2.end_at - end_after_first
    # ± 5s 容忍 (apply_paid_order 内部用 max(now, end_at) 起算)
    assert timedelta(days=30) - timedelta(seconds=5) <= delta <= timedelta(days=30) + timedelta(seconds=5)
    assert snap2.total_paid_cny == Decimal("78.00")
    assert snap2.status == "active"


# ─── 9. expired → active 覆盖 ──────────────────────────────────────────


async def test_apply_paid_order_replace_expired(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    user_id = await _seed_user(session_factory, phone_suffix="0009")
    # 直接埋一行 expired
    async with session_factory() as s:
        old_order = VipOrder(
            user_id=user_id,
            out_trade_no=f"XGZH-OLD-{uuid.uuid4().hex[:8]}",
            plan="trial",
            amount_cny=Decimal("0"),
            status="paid",
            payment_channel="internal",
            paid_at=datetime.now(UTC) - timedelta(days=10),
        )
        s.add(old_order)
        await s.flush()
        m = VipMembership(
            user_id=user_id,
            status="expired",
            plan="trial",
            start_at=datetime.now(UTC) - timedelta(days=10),
            end_at=datetime.now(UTC) - timedelta(days=3),
            current_order_id=old_order.order_id,
            total_paid_cny=Decimal("0"),
        )
        s.add(m)
        await s.commit()

    async with session_factory() as s:
        order = VipOrder(
            user_id=user_id,
            out_trade_no=f"XGZH-{uuid.uuid4().hex[:12]}",
            plan="quarterly",
            amount_cny=Decimal("99.00"),
            status="paid",
            payment_channel="wechat_mp",
            paid_at=datetime.now(UTC),
        )
        s.add(order)
        await s.flush()
        snap = await vip_service.apply_paid_order(s, user_id=user_id, order=order)
        await s.commit()

    assert snap.status == "active"
    assert snap.plan == "quarterly"
    expected_end = datetime.now(UTC) + timedelta(days=90)
    assert abs((snap.end_at - expected_end).total_seconds()) < 5


# ─── 10. lifetime end_at = 9999-12-31 ──────────────────────────────────


async def test_apply_paid_order_lifetime(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    user_id = await _seed_user(session_factory, phone_suffix="0010")
    async with session_factory() as s:
        u = (
            await s.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()
        await vip_service.grant_trial(s, u)
        order = VipOrder(
            user_id=user_id,
            out_trade_no=f"XGZH-{uuid.uuid4().hex[:12]}",
            plan="lifetime",
            amount_cny=Decimal("999.00"),
            status="paid",
            payment_channel="wechat_mp",
            paid_at=datetime.now(UTC),
        )
        s.add(order)
        await s.flush()
        snap = await vip_service.apply_paid_order(s, user_id=user_id, order=order)
        await s.commit()

    assert snap.status == "active"
    assert snap.plan == "lifetime"
    assert snap.end_at.year == 9999
    assert snap.end_at.month == 12


# ─── 11. expire_overdue 把过期试用标 expired ───────────────────────────


async def test_expire_overdue_marks_expired(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    # user A: 试用过期
    user_a = await _seed_user(session_factory, phone_suffix="0011")
    # user B: 试用未过期 (对照组, 不应受影响)
    user_b = await _seed_user(session_factory, phone_suffix="0012")
    async with session_factory() as s:
        for uid, end_offset_days in ((user_a, -1), (user_b, +5)):
            order = VipOrder(
                user_id=uid,
                out_trade_no=f"XGZH-TRIAL-{uuid.uuid4().hex[:8]}",
                plan="trial",
                amount_cny=Decimal("0"),
                status="paid",
                payment_channel="internal",
                paid_at=datetime.now(UTC),
            )
            s.add(order)
            await s.flush()
            m = VipMembership(
                user_id=uid,
                status="trialing",
                plan="trial",
                start_at=datetime.now(UTC) - timedelta(days=7),
                end_at=datetime.now(UTC) + timedelta(days=end_offset_days),
                current_order_id=order.order_id,
                total_paid_cny=Decimal("0"),
            )
            s.add(m)
        await s.commit()

    result = await vip_service.expire_overdue_memberships()
    assert result.expired == 1

    async with session_factory() as s:
        ma = (
            await s.execute(
                select(VipMembership).where(VipMembership.user_id == user_a)
            )
        ).scalar_one()
        mb = (
            await s.execute(
                select(VipMembership).where(VipMembership.user_id == user_b)
            )
        ).scalar_one()
    assert ma.status == "expired"
    assert mb.status == "trialing"  # 未过期保持原状


# ─── 12. /vip/me 在 expire 后返历史信息 + has_active=False ──────────────


async def test_vip_me_after_expire_shows_history(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    user_id_str, token = await _register_user_via_otp(
        client, phone=f"+8613800138{uuid.uuid4().int % 1000:03d}"
    )
    user_id = uuid.UUID(user_id_str)
    # 强制把试用 end_at 推到过去
    async with session_factory() as s:
        m = (
            await s.execute(
                select(VipMembership).where(VipMembership.user_id == user_id)
            )
        ).scalar_one()
        m.end_at = datetime.now(UTC) - timedelta(hours=1)
        await s.commit()

    await vip_service.expire_overdue_memberships()

    resp = await client.get(
        "/api/v1/vip/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_active"] is False
    assert body["status"] == "expired"  # 历史信息可见
    assert body["plan"] == "trial"
    assert body["days_remaining"] is None


# ─── 13. trialing user → resolve_plan_with_membership = VIP ─────────────


async def test_resolve_plan_with_membership_trialing_is_vip(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    user_id = await _seed_user(session_factory, phone_suffix="0013")
    async with session_factory() as s:
        u = (
            await s.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()
        await vip_service.grant_trial(s, u)
        await s.commit()

    async with session_factory() as s:
        u = (
            await s.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()

    plan = await _resolve_plan_with_membership(u)
    assert plan is QuotaPlan.VIP


# ─── 14. expired user → FREE ───────────────────────────────────────────


async def test_resolve_plan_expired_user_is_free(
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    user_id = await _seed_user(session_factory, phone_suffix="0014")
    async with session_factory() as s:
        order = VipOrder(
            user_id=user_id,
            out_trade_no=f"XGZH-OLD-{uuid.uuid4().hex[:8]}",
            plan="trial",
            amount_cny=Decimal("0"),
            status="paid",
            payment_channel="internal",
            paid_at=datetime.now(UTC) - timedelta(days=10),
        )
        s.add(order)
        await s.flush()
        m = VipMembership(
            user_id=user_id,
            status="expired",
            plan="trial",
            start_at=datetime.now(UTC) - timedelta(days=10),
            end_at=datetime.now(UTC) - timedelta(days=3),
            current_order_id=order.order_id,
            total_paid_cny=Decimal("0"),
        )
        s.add(m)
        await s.commit()

    async with session_factory() as s:
        u = (
            await s.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()

    plan = await _resolve_plan_with_membership(u)
    assert plan is QuotaPlan.FREE


# ─── 15. 白名单兜底 (无 membership 行也走 VIP) ──────────────────────────


async def test_resolve_plan_whitelist_fallback(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """settings.vip_user_id_whitelist 命中 → 不查表直接 VIP, 兼容 dev / 紧急场景."""
    user_id = await _seed_user(session_factory, phone_suffix="0015")
    # mock get_settings 走白名单
    from app.core.config import get_settings

    real = get_settings()
    monkeypatch.setattr(
        real,
        "vip_user_id_whitelist",
        str(user_id),
    )
    # cached_property: 清掉 cache
    if "vip_user_id_set" in real.__dict__:
        del real.__dict__["vip_user_id_set"]

    async with session_factory() as s:
        u = (
            await s.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()

    plan = await _resolve_plan_with_membership(u)
    assert plan is QuotaPlan.VIP, "白名单命中应跳过 DB 查询直接 VIP"
