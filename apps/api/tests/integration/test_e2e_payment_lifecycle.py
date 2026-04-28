"""QA-S3-002: 微信支付 v3 沙箱 + 订阅生命周期 e2e 集成测.

定位
====
覆盖 BE-S3-009 + 010 全链路 — 注册 → 试用 → 升级下单 → Stub 沙箱回调验签 →
membership 状态机流转 → quota 配额放开. 所有用例都通过 HTTP 端点走完整路径
(``/auth/login/phone`` → ``/pay/wechat/order`` → ``/pay/wechat/notify`` →
``/vip/me`` + quota.check_quota), 而非各阶段独立验证 (那些已在
``test_payment_e2e.py`` / ``test_vip_lifecycle.py`` 里覆盖).

为什么再开一个文件
==================
- ``test_payment_e2e.py`` 验各 endpoint 行为 (per-API)
- ``test_vip_lifecycle.py`` 验 vip_service 状态机 (per-service)

但 spec/10 §QA-S3-002 锁定 5 条 *journey* 用例: 必须验证 "用户从注册到付费,
经过 trialing → paid → active → 续费, 整条 HTTP 链路上每跳的状态机都自洽".
这种 cross-stage / cross-service 的 *journey assertion* 在单 stage 文件里
写起来分裂.

测试用例 (与 spec/10 §QA-S3-002 一一对齐)
=========================================
1.  ``test_lifecycle_register_to_paid_active`` — 金线: 注册 → trialing →
    月度下单 → 沙箱回调 SUCCESS → membership.status='active' /
    end_at = now + 30d → quota.check_quota 走 VIP 无限
2.  ``test_lifecycle_renewal_stacks_end_at`` — 续费堆叠: active 用户人为锁定
    end_at = now + 60d → 第二笔月度下单 → 回调 → end_at += 30d (从锁定
    end_at 起算, 不从 now), total_paid_cny 累加
3.  ``test_lifecycle_trialing_replaced_by_paid`` — 试用立即结束: trialing
    用户人为锁定 end_at = now + 5d (剩 5 天试用) → 月度下单 → 回调 →
    end_at = now + 30d (而非 now + 5 + 30 = 35d), 验证 spec/06 §2.3
    "trial 立即结束不堆叠" 行为
4.  ``test_lifecycle_callback_idempotent_no_double_extend`` — 回调幂等:
    同 transaction_id 二次回调 → 200 SUCCESS + membership 不重复加 30d +
    total_paid_cny 不翻倍 (微信网络抖动 / 重投保护)
5.  ``test_lifecycle_signature_failure_keeps_pending_and_trialing`` —
    验签失败: 回调不带 ``X-Stub-Sign-Override`` header → 200 + code='FAIL' +
    order.status='pending' + membership.status='trialing' (验签闸门挡住,
    业务侧零副作用)

依赖
====
- BE-S3-009 (vip_service / membership 状态机)
- BE-S3-010 (微信支付 v3 + StubWechatPayClient + 回调验签 + 配额接真)

不验
====
- 真实微信沙箱 (``WECHATPAY_DEV_MODE=false`` 走 RealWechatPayClient, 单测
  覆盖 + 灰度联调阶段验证)
- 4 种 plan 的金额对齐 (已在 ``test_payment_e2e.py`` parametrize 验过)
- 各种 trade_state (PAYERROR / 孤儿 out_trade_no 已在 ``test_payment_e2e.py`` 验过)
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import User, VipMembership, VipOrder
from app.services import otp_service
from app.services.agent.quota import (
    QuotaPlan,
    _resolve_plan_with_membership,
    check_quota,
)
from app.services.vip_service import PLAN_DURATION_DAYS

pytestmark = pytest.mark.db


# ─── 4 种回调 fixture (spec/10 §QA-S3-002 锁定) ─────────────────────────


def _build_callback_body(
    *,
    out_trade_no: str,
    transaction_id: str,
    amount_cents: int,
    trade_state: str = "SUCCESS",
    openid: str = "stub_openid_lifecycle",
    success_time: str = "2026-04-28T12:00:00+08:00",
) -> bytes:
    """构造微信支付 v3 回调 payload (Stub 模式: 直接 JSON, 不走 AES-GCM 解密).

    与 ``scripts/dev_wechatpay_simulate_callback.py`` 同款字段; 复刻而非
    import 是为了让 e2e 用例的 callback 字段约束清晰可读.
    """
    payload = {
        "id": "EVT-LIFECYCLE-1",
        "create_time": success_time,
        "event_type": "TRANSACTION.SUCCESS",
        "resource_type": "encrypt-resource",
        "summary": "支付成功",
        "out_trade_no": out_trade_no,
        "transaction_id": transaction_id,
        "trade_state": trade_state,
        "amount": {
            "total": amount_cents,
            "payer_total": amount_cents,
            "currency": "CNY",
        },
        "payer": {"openid": openid},
        "success_time": success_time,
    }
    return json.dumps(payload).encode("utf-8")


def callback_fixture_success(
    *, out_trade_no: str, transaction_id: str, amount_cents: int
) -> tuple[bytes, dict[str, str]]:
    """fixture 1: 成功回调 (trade_state=SUCCESS + 带验签 bypass header)."""
    body = _build_callback_body(
        out_trade_no=out_trade_no,
        transaction_id=transaction_id,
        amount_cents=amount_cents,
        trade_state="SUCCESS",
    )
    headers = {
        "Content-Type": "application/json",
        "X-Stub-Sign-Override": "bypass",
    }
    return body, headers


def callback_fixture_payerror(
    *, out_trade_no: str, transaction_id: str, amount_cents: int
) -> tuple[bytes, dict[str, str]]:
    """fixture 2: 支付失败 (trade_state=PAYERROR + 带验签 bypass header).

    Stub 解析后会写 order.status='failed', 但 v3 协议层仍返 200 SUCCESS
    (拒绝重投; 让微信不再发).
    """
    body = _build_callback_body(
        out_trade_no=out_trade_no,
        transaction_id=transaction_id,
        amount_cents=amount_cents,
        trade_state="PAYERROR",
    )
    headers = {
        "Content-Type": "application/json",
        "X-Stub-Sign-Override": "bypass",
    }
    return body, headers


def callback_fixture_signature_invalid(
    *, out_trade_no: str, transaction_id: str, amount_cents: int
) -> tuple[bytes, dict[str, str]]:
    """fixture 3: 验签错误 (无 ``X-Stub-Sign-Override`` header).

    走完整 v3 协议: ``StubWechatPayClient.verify_signature`` 在 dev 模式下
    要求该 header 存在; 缺失 → 视为伪造请求, 路由层返 200 + code='FAIL'.
    """
    body = _build_callback_body(
        out_trade_no=out_trade_no,
        transaction_id=transaction_id,
        amount_cents=amount_cents,
        trade_state="SUCCESS",
    )
    headers = {"Content-Type": "application/json"}  # 故意不带 bypass header
    return body, headers


def callback_fixture_replay_idempotent(
    *,
    out_trade_no: str,
    transaction_id: str,
    amount_cents: int,
    timestamp: str = "2026-04-28T12:00:00+08:00",
) -> tuple[bytes, dict[str, str]]:
    """fixture 4: 重投幂等 (同 ``out_trade_no`` + 同 ``transaction_id`` 相同 body).

    与 ``callback_fixture_success`` 等价, 但单独露出 timestamp 让两次调用
    的 ``success_time`` 字段一致 — 验证 service 层 ``transaction_id``
    唯一约束起到幂等闸门.
    """
    body = _build_callback_body(
        out_trade_no=out_trade_no,
        transaction_id=transaction_id,
        amount_cents=amount_cents,
        trade_state="SUCCESS",
        success_time=timestamp,
    )
    headers = {
        "Content-Type": "application/json",
        "X-Stub-Sign-Override": "bypass",
    }
    return body, headers


# ─── 共用 helpers ──────────────────────────────────────────────────────────


async def _register_via_otp(
    client: httpx.AsyncClient,
    *,
    phone: str,
    code: str = "654321",
) -> tuple[uuid.UUID, str]:
    """走完整 OTP → /auth/login/phone, 返 (user_id, access_token).

    注册成功后服务端会自动给用户 grant_trial → membership.status='trialing',
    is_new_user=True 时端层落 trial 订单一笔.
    """
    await otp_service.store_otp(phone, code, ttl_seconds=300)
    resp = await client.post(
        "/api/v1/auth/login/phone", json={"phone": phone, "code": code}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_new_user"] is True, "首次注册必须 is_new_user=True"
    return uuid.UUID(body["user"]["user_id"]), body["tokens"]["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_monthly_order(
    client: httpx.AsyncClient, token: str
) -> str:
    """下月度订单, 返 ``out_trade_no``. 单元复用; 失败直接 assert."""
    resp = await client.post(
        "/api/v1/pay/wechat/order",
        json={"plan": "monthly"},
        headers=_auth_headers(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    out_trade_no: str = body["out_trade_no"]
    return out_trade_no


async def _force_membership_end_at(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    end_at: datetime,
    status: str | None = None,
) -> None:
    """绕过 service 直接改 ``membership.end_at`` (+ 可选 status).

    用途: case 2 / 3 需要"现 end_at"是已知确定值才能验证堆叠 / 覆盖逻辑.
    生产路径 ``apply_paid_order`` 会基于 end_at 算下一档, 测试侧固定 end_at
    后能精确断言 ±5s 误差.
    """
    values: dict[str, datetime | str] = {"end_at": end_at}
    if status is not None:
        values["status"] = status
    async with factory() as s:
        await s.execute(
            update(VipMembership)
            .where(VipMembership.user_id == user_id)
            .values(**values)
        )
        await s.commit()


async def _fetch_membership(
    factory: async_sessionmaker[AsyncSession], *, user_id: uuid.UUID
) -> VipMembership:
    async with factory() as s:
        return (
            await s.execute(
                select(VipMembership).where(VipMembership.user_id == user_id)
            )
        ).scalar_one()


async def _fetch_order_by_out_trade_no(
    factory: async_sessionmaker[AsyncSession], *, out_trade_no: str
) -> VipOrder:
    async with factory() as s:
        return (
            await s.execute(
                select(VipOrder).where(VipOrder.out_trade_no == out_trade_no)
            )
        ).scalar_one()


# ─── 1. 金线: 注册 → trialing → 月度付费 → active + VIP 配额 ──────────────


async def test_lifecycle_register_to_paid_active(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001 — quota 接真 + apply_paid_order 用
) -> None:
    """完整金线 journey:

    Step 1: ``/auth/login/phone`` 注册 → membership.status='trialing'
    Step 2: ``/pay/wechat/order monthly`` → 落 pending order + 拿 paySign
    Step 3: ``/pay/wechat/notify`` Stub 回调 SUCCESS → 走 v3 验签 →
            order.status='paid' → ``apply_paid_order`` → membership 流转
            ``trialing → active``
    Step 4: 验 DB 终态: end_at ≈ now + 30d, total_paid_cny=39.00,
            current_order_id 指向本笔订单
    Step 5: 验 ``quota.check_quota`` → plan=VIP, limit=-1 (无限)
    Step 6: 验 ``GET /vip/me`` → has_active=True / status='active' /
            days_remaining ≈ 30
    """
    # Step 1: 注册 (自动 trialing)
    user_id, token = await _register_via_otp(client, phone="+8613800200001")
    membership_initial = await _fetch_membership(session_factory, user_id=user_id)
    assert membership_initial.status == "trialing"
    assert membership_initial.plan == "trial"

    # Step 2: 下单
    out_trade_no = await _create_monthly_order(client, token)
    pending = await _fetch_order_by_out_trade_no(
        session_factory, out_trade_no=out_trade_no
    )
    assert pending.status == "pending"
    assert pending.amount_cny == Decimal("39.00")

    # Step 3: 沙箱回调 (fixture 1: success)
    body, headers = callback_fixture_success(
        out_trade_no=out_trade_no,
        transaction_id="WX-LIFE-001",
        amount_cents=3900,
    )
    cb = await client.post("/api/v1/pay/wechat/notify", content=body, headers=headers)
    assert cb.status_code == 200
    assert cb.json() == {"code": "SUCCESS", "message": "OK"}, cb.text

    # Step 4: DB 状态
    paid = await _fetch_order_by_out_trade_no(
        session_factory, out_trade_no=out_trade_no
    )
    assert paid.status == "paid"
    assert paid.transaction_id == "WX-LIFE-001"
    assert paid.paid_at is not None

    membership_after = await _fetch_membership(session_factory, user_id=user_id)
    assert membership_after.status == "active"
    assert membership_after.plan == "monthly"
    assert membership_after.current_order_id == paid.order_id
    assert membership_after.total_paid_cny == Decimal("39.00")
    expected_end = datetime.now(UTC) + timedelta(days=PLAN_DURATION_DAYS["monthly"])
    delta_seconds = abs((membership_after.end_at - expected_end).total_seconds())
    assert delta_seconds < 60, (
        f"end_at 应 ≈ now + 30d, 实际差 {delta_seconds}s "
        f"(end_at={membership_after.end_at}, expected={expected_end})"
    )

    # Step 5: quota 配额接真 → VIP 无限
    async with session_factory() as s:
        u = (
            await s.execute(select(User).where(User.user_id == user_id))
        ).scalar_one()
    plan = await _resolve_plan_with_membership(u)
    assert plan is QuotaPlan.VIP

    status = await check_quota(user=u)
    assert status.plan is QuotaPlan.VIP
    assert status.limit == -1, f"VIP 应该无限, 实际 limit={status.limit}"
    assert status.remaining == -1
    assert status.retry_after_seconds is None

    # Step 6: HTTP /vip/me 反映 active
    me = await client.get("/api/v1/vip/me", headers=_auth_headers(token))
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["has_active"] is True
    assert me_body["status"] == "active"
    assert me_body["plan"] == "monthly"
    assert 29 <= me_body["days_remaining"] <= 30


# ─── 2. 续费堆叠: 现 end_at + 30d (从锁定 end_at 起算, 不从 now) ──────────


async def test_lifecycle_renewal_stacks_end_at(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """active 用户续费 → end_at 按 *现 end_at* 起算 + 30d, 不从 now 起算.

    场景模拟用户买完月度还有 60d 没用, 又下一单:
    - 第 1 单: trialing → active (end_at = now + 30d, mocked 后人为锁 now + 60d)
    - 第 2 单: active → active (end_at = (now + 60d) + 30d = now + 90d)
    - total_paid_cny: 39 + 39 = 78

    与 ``test_payment_e2e.test_callback_active_user_renewal_stacks_end_at`` 不同点:
    本用例显式锁定 ``end_at`` 让 stacking 计算可精确断言 (那个测试有 SKIP 路径,
    因为 5min 幂等窗导致第二单可能复用第一单 — 这里第一单已 paid 不再 pending,
    第二单一定是新订单, 不会触发幂等复用).
    """
    user_id, token = await _register_via_otp(client, phone="+8613800200002")

    # 第 1 单: 月度付费 → trialing → active
    out_trade_no_1 = await _create_monthly_order(client, token)
    body1, headers1 = callback_fixture_success(
        out_trade_no=out_trade_no_1,
        transaction_id="WX-LIFE-RENEW-1",
        amount_cents=3900,
    )
    cb1 = await client.post(
        "/api/v1/pay/wechat/notify", content=body1, headers=headers1
    )
    assert cb1.json() == {"code": "SUCCESS", "message": "OK"}

    # 人为锁定 end_at = now + 60d (固定可预测的"现 end_at")
    locked_end_at = datetime.now(UTC) + timedelta(days=60)
    await _force_membership_end_at(
        session_factory, user_id=user_id, end_at=locked_end_at
    )

    # 第 2 单: active 续费 — 第 1 单已 paid 不再 pending, 不会触发幂等复用
    out_trade_no_2 = await _create_monthly_order(client, token)
    assert out_trade_no_2 != out_trade_no_1, (
        "第 1 单已 paid, 第 2 单必须是新 out_trade_no (idempotency 仅复用 pending)"
    )
    body2, headers2 = callback_fixture_success(
        out_trade_no=out_trade_no_2,
        transaction_id="WX-LIFE-RENEW-2",
        amount_cents=3900,
    )
    cb2 = await client.post(
        "/api/v1/pay/wechat/notify", content=body2, headers=headers2
    )
    assert cb2.json() == {"code": "SUCCESS", "message": "OK"}

    membership = await _fetch_membership(session_factory, user_id=user_id)
    assert membership.status == "active"
    assert membership.plan == "monthly"
    assert membership.total_paid_cny == Decimal("78.00")  # 39 + 39

    expected_end = locked_end_at + timedelta(days=PLAN_DURATION_DAYS["monthly"])
    delta = abs((membership.end_at - expected_end).total_seconds())
    assert delta < 5, (
        f"end_at 应 = locked_end_at(+60d) + 30d, 实际 {membership.end_at}, "
        f"期望 {expected_end} (差 {delta}s)"
    )


# ─── 3. 试用立即结束: trialing 切付费 不堆叠剩余试用天数 ────────────────


async def test_lifecycle_trialing_replaced_by_paid(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """spec/06 §2.3: trialing 用户付费 → 试用立即结束, end_at 重置为
    ``now + plan duration``, 不堆叠剩余试用天数.

    场景: 用户注册 5 天后 (剩 2 天试用) 付费转月度
    - 模拟方式: 锁 end_at = now + 5d, status='trialing'
    - 月度付费 → 回调 SUCCESS
    - 期望: end_at = now + 30d (而非 now + 5 + 30 = 35d)

    与 ``test_vip_lifecycle.test_apply_paid_order_replace_trialing`` 互补:
    那个直连 service 验状态机, 本用例走完整 HTTP 链路 (含路由 / 验签 / quota
    联动), 守住"v3 协议层不会改变状态机契约"的回归.
    """
    user_id, token = await _register_via_otp(client, phone="+8613800200003")

    # 锁 end_at = now + 5d (剩 5 天试用)
    locked_trial_end = datetime.now(UTC) + timedelta(days=5)
    await _force_membership_end_at(
        session_factory, user_id=user_id, end_at=locked_trial_end
    )
    membership_before = await _fetch_membership(session_factory, user_id=user_id)
    assert membership_before.status == "trialing"

    # 月度付费 → 回调 SUCCESS
    out_trade_no = await _create_monthly_order(client, token)
    body, headers = callback_fixture_success(
        out_trade_no=out_trade_no,
        transaction_id="WX-LIFE-TRIAL2PAID",
        amount_cents=3900,
    )
    cb = await client.post("/api/v1/pay/wechat/notify", content=body, headers=headers)
    assert cb.json() == {"code": "SUCCESS", "message": "OK"}

    membership_after = await _fetch_membership(session_factory, user_id=user_id)
    assert membership_after.status == "active"
    assert membership_after.plan == "monthly"
    # end_at = now + 30d, 不是 now + 5 + 30 = 35d
    expected_end = datetime.now(UTC) + timedelta(days=PLAN_DURATION_DAYS["monthly"])
    delta = abs((membership_after.end_at - expected_end).total_seconds())
    assert delta < 60, (
        f"trialing 切 paid 应覆盖式重置 end_at = now + 30d (不堆叠 5d 试用), "
        f"实际 {membership_after.end_at}, 期望 {expected_end}, 差 {delta}s"
    )

    # 反向断言: end_at 离 (now + 35d) 至少差 5d → 确认未堆叠
    stacked_wrong = datetime.now(UTC) + timedelta(days=35)
    assert abs((membership_after.end_at - stacked_wrong).total_seconds()) > 86_400 * 4, (
        "若错误堆叠了试用 5d, end_at 会接近 now + 35d; 此时本断言应失败"
    )


# ─── 4. 回调幂等: 同 transaction_id 二次回调不重复加 30d ─────────────────


async def test_lifecycle_callback_idempotent_no_double_extend(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """微信网络抖动 / 商户响应慢 → 微信会重投同 transaction_id 回调 (BE-S3-010
    §3 重投幂等闸门要求).

    验证:
    - 第 1 次回调: 200 SUCCESS + order paid + membership end_at_1
    - 第 2 次回调 (同 body): 200 SUCCESS + order 仍 paid + membership end_at
      不变 (delta < 5s, 容忍 PG ts 微秒级误差) + total_paid_cny 仍 = 39
    """
    user_id, token = await _register_via_otp(client, phone="+8613800200004")

    out_trade_no = await _create_monthly_order(client, token)
    body, headers = callback_fixture_replay_idempotent(
        out_trade_no=out_trade_no,
        transaction_id="WX-LIFE-IDEMPOTENT",
        amount_cents=3900,
    )

    # 第 1 次
    r1 = await client.post(
        "/api/v1/pay/wechat/notify", content=body, headers=headers
    )
    assert r1.json() == {"code": "SUCCESS", "message": "OK"}
    m1 = await _fetch_membership(session_factory, user_id=user_id)
    assert m1.status == "active"
    end_at_1 = m1.end_at
    paid_at_1 = m1.created_at  # 用 created_at 作 sentinel; end_at 是关键

    # 第 2 次 (相同 body / headers / transaction_id)
    r2 = await client.post(
        "/api/v1/pay/wechat/notify", content=body, headers=headers
    )
    assert r2.json() == {"code": "SUCCESS", "message": "OK"}, (
        f"重投也应返 SUCCESS (而非 FAIL, 否则微信会无限重试), 实际 {r2.text}"
    )
    m2 = await _fetch_membership(session_factory, user_id=user_id)

    # 关键断言: end_at / total_paid_cny 不变
    assert m2.end_at == end_at_1, (
        f"重投不应再加 30d, 期望 end_at 不变, 实际 {m2.end_at} vs {end_at_1}"
    )
    assert m2.total_paid_cny == Decimal("39.00"), (
        f"重投不应翻倍 total_paid_cny, 期望 39, 实际 {m2.total_paid_cny}"
    )
    # created_at 也不应变 (membership 同一行)
    assert m2.created_at == paid_at_1


# ─── 5. 验签失败: 验签闸门挡住 → 业务零副作用 ──────────────────────────


async def test_lifecycle_signature_failure_keeps_pending_and_trialing(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """伪造 / 篡改的回调 (无 ``X-Stub-Sign-Override`` header) → 路由层
    ``StubWechatPayClient.verify_signature`` 返 False → 200 + code='FAIL'
    + 业务零副作用:
    - order.status 仍 'pending'
    - membership.status 仍 'trialing'
    - GET /vip/me 仍返 trialing 状态

    返 200 (而非 4xx) 的原因: v3 协议要求验签失败也走 200 + body code='FAIL',
    让微信侧不会因 HTTP 4xx 误判商户 outage; 详见 ``BE-S3-010 §回调路由``.
    """
    user_id, token = await _register_via_otp(client, phone="+8613800200005")

    out_trade_no = await _create_monthly_order(client, token)
    pending_before = await _fetch_order_by_out_trade_no(
        session_factory, out_trade_no=out_trade_no
    )
    assert pending_before.status == "pending"

    # fixture 3: 验签错误 (无 bypass header)
    body, headers = callback_fixture_signature_invalid(
        out_trade_no=out_trade_no,
        transaction_id="WX-LIFE-SIG-FAIL",
        amount_cents=3900,
    )
    cb = await client.post("/api/v1/pay/wechat/notify", content=body, headers=headers)
    assert cb.status_code == 200, "验签失败也走 200 (v3 协议)"
    assert cb.json() == {"code": "FAIL", "message": "signature verify failed"}

    # 业务零副作用
    order_after = await _fetch_order_by_out_trade_no(
        session_factory, out_trade_no=out_trade_no
    )
    assert order_after.status == "pending", (
        f"验签失败不应改 order.status, 实际 {order_after.status}"
    )
    assert order_after.transaction_id is None
    assert order_after.paid_at is None

    membership_after = await _fetch_membership(session_factory, user_id=user_id)
    assert membership_after.status == "trialing", (
        "验签失败不应触发 apply_paid_order; membership 应仍 trialing"
    )

    # /vip/me 也返 trialing
    me = await client.get("/api/v1/vip/me", headers=_auth_headers(token))
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["status"] == "trialing"
    assert me_body["plan"] == "trial"
