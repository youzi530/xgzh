"""微信支付业务编排 (BE-S3-010).

3 大职能
========
1. ``create_wechat_jsapi_order(user, plan, channel)`` — 下单:
   ① 校验 plan / channel + 拿 openid (wechat_mp 必须有 wechat_openid)
   ② 5 min 幂等窗内复用现有 pending 订单 (防双击)
   ③ 否则: 写 ``vip_orders(status='pending')`` + 调 ``WechatPayClient.create_jsapi_order``
   ④ 返 (order, payment_params) — 路由层组装 ``CreateOrderResponse``

2. ``handle_wechat_callback(headers, body)`` — 回调:
   ① ``WechatPayClient.verify_and_decrypt_callback`` 验签 + 解密
   ② 失败 → 返 ``("FAIL", "signature verify failed")``, 路由层 200 + body 走 ``NotifyResponse``
   ③ 成功:
      - 取 ``out_trade_no`` 反查订单; 不存在 → log warn 返 SUCCESS 不重投
      - 已 ``status='paid'`` → 幂等命中, 返 SUCCESS 不重处理
      - ``trade_state != SUCCESS`` → 落 ``status='failed'``, 返 SUCCESS
      - ``trade_state == SUCCESS`` 且金额匹配:
        ``status='paid'`` + ``transaction_id`` + ``paid_at`` + ``raw_callback`` →
        调 ``vip_service.apply_paid_order`` 驱动 membership 状态机
      - 金额不匹配 → 严重报警 log + 返 FAIL (微信会重试, 让人工介入)

3. ``PLAN_PRICES_CNY`` — 价目表; spec/06 §2.2 原价标准:
   monthly=39 / quarterly=99 / yearly=299 / lifetime=999

设计取舍
========
- **价目表服务端权威**: 前端不传金额, 服务端按 plan 反查; 防止前端篡改
- **回调任何路径都返 SUCCESS** (除验签失败 + 金额不一致) — 微信侧重试机制粗暴,
  失败原因若是业务可恢复 (如订单不存在、user 已删) 让微信"重试"也无意义,
  直接 ack 让我们处理掉 (SUCCESS 表示"我已经收到, 不要再发了")
- **不在回调里 commit user 改动** — 状态机走 ``vip_service.apply_paid_order``
  内部事务, 失败可整体回滚不写脏 ``status=paid`` 而 membership 没流转
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select

from app.core.config import get_settings
from app.core.logging import logger
from app.db import get_session_factory
from app.db.models import User, VipOrder
from app.schemas.payment import (
    PayablePlanLiteral,
    PaymentChannelLiteral,
    PaymentParams,
)
from app.services import vip_service
from app.services.payment.wechat_client import (
    CallbackPayload,
    WechatPayError,
    get_client_for_request,
)

# ─── 常量 ────────────────────────────────────────────────────────────────

PLAN_PRICES_CNY: dict[str, Decimal] = {
    "monthly": Decimal("39.00"),
    "quarterly": Decimal("99.00"),
    "yearly": Decimal("299.00"),
    "lifetime": Decimal("999.00"),
}
"""spec/06 §2.2 原价 (CNY) — 与 ``apps/mp/pages/vip/index.vue`` 套餐卡价一一对应.

促销价 (首年优惠) 走另一套 ``promo_prices`` 表 (Sprint 4 上线营销活动时再加),
当前 PR 不动促销逻辑.
"""

PLAN_DESCRIPTIONS: dict[str, str] = {
    "monthly": "新股智汇 VIP 月度订阅",
    "quarterly": "新股智汇 VIP 季度订阅",
    "yearly": "新股智汇 VIP 年度订阅",
    "lifetime": "新股智汇 VIP 终身订阅",
}
"""微信支付商户后台账单 + 用户支付凭证里展示的商品描述. 中文 ≤ 127 字 (微信限制 127)."""


class PaymentError(Exception):
    """支付层业务错误. 路由层捕获 → HTTPException(4xx/5xx).

    错误代码 (``code`` 字段) 给前端做 UI 决策:
    - ``invalid_plan``        → 4xx, plan 不在合法集 (前端 enum 漂移)
    - ``unsupported_channel`` → 4xx, channel 不支持 (Sprint 3 仅 wechat_mp)
    - ``no_wechat_openid``    → 4xx, 用户未走过微信小程序登录 (得先扫码登录)
    - ``wechat_pay_error``    → 5xx, SDK 调用失败 / 网络异常
    - ``wechat_pay_unconfigured`` → 5xx, 商户号未配置 (运维 / 部署问题)
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


# ─── 1. 下单 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CreateOrderResult:
    """``create_wechat_jsapi_order`` 返回结构, 路由层用来组 ``CreateOrderResponse``."""

    order_id: uuid.UUID
    out_trade_no: str
    plan: str
    amount_cny: Decimal
    payment_channel: str
    payment_params: PaymentParams
    created_at: datetime


def _generate_out_trade_no() -> str:
    """``XGZH<14digit_timestamp><6char_rand>`` ≤ 32 字, 走 ``vip_orders.out_trade_no`` UNIQUE.

    格式约束 (微信 v3 要求): ASCII 6-32 字, 仅字母数字 / ``_-*``. 我们走纯字母数字.
    时间戳精确到毫秒不放, 1 秒精度 + 6 char rand 已足够防碰撞 (12M+ 唯一 / 秒).
    """
    ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")  # 14 字
    rand = secrets.token_hex(3).upper()  # 6 字大写 hex
    return f"XGZH{ts}{rand}"


async def create_wechat_jsapi_order(
    user: User,
    *,
    plan: PayablePlanLiteral,
    payment_channel: PaymentChannelLiteral = "wechat_mp",
) -> CreateOrderResult:
    """JSAPI 下单 (小程序内 ``uni.requestPayment`` 路径).

    流程:
    1. 校验 plan + channel
    2. ``wechat_mp`` 走 JSAPI: 必须有 ``user.wechat_openid`` (走过 /auth/login/wechat-mp)
    3. 幂等窗口检查: 同 user + 同 plan, 5 min 内有 ``status='pending'`` 订单 → 复用
       (防用户连点; 复用走原 out_trade_no, 走原 prepay_id 不重新调微信)
    4. 否则: 写 ``vip_orders(status='pending')`` + 调 ``WechatPayClient.create_jsapi_order``
    5. 返 ``CreateOrderResult``

    Args:
        user: 当前认证用户 (必须存在, 走 ``Depends(get_current_user)``)
        plan: 套餐 ID; 必须在 ``PLAN_PRICES_CNY`` 集
        payment_channel: 支付渠道; 当前仅 ``wechat_mp``

    Raises:
        PaymentError: 业务路径错误 (路由层转 HTTPException)
    """
    if plan not in PLAN_PRICES_CNY:
        raise PaymentError("invalid_plan", f"unknown plan: {plan!r}")
    if payment_channel != "wechat_mp":
        raise PaymentError(
            "unsupported_channel",
            f"channel {payment_channel!r} not supported in Sprint 3 (only wechat_mp)",
        )

    client = get_client_for_request()

    # JSAPI 必须有 openid; Stub 走 dev mode 时允许 'stub_openid' 兜底, 但仍校验 user 维度
    openid = user.wechat_openid
    if not openid:
        if client.is_stub:
            # Stub 模式: 用 user_id hash 派生稳定 openid (单测可断言)
            openid = f"stub_openid_{user.user_id.hex[:16]}"
        else:
            raise PaymentError(
                "no_wechat_openid",
                "user has not bound WeChat MP openid; please login via /auth/login/wechat-mp first",
            )

    amount_cny = PLAN_PRICES_CNY[plan]
    description = PLAN_DESCRIPTIONS[plan]

    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session, session.begin():
        # 幂等窗口检查
        idempotency_window_seconds = settings.wechatpay_order_idempotency_seconds
        if idempotency_window_seconds > 0:
            since = datetime.now(UTC) - timedelta(seconds=idempotency_window_seconds)
            existing = (
                await session.execute(
                    select(VipOrder).where(
                        and_(
                            VipOrder.user_id == user.user_id,
                            VipOrder.plan == plan,
                            VipOrder.status == "pending",
                            VipOrder.payment_channel == payment_channel,
                            VipOrder.created_at >= since,
                        )
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                # 复用旧 pending 订单, 但 paySign 时间戳得重新签 (微信限制 5 min 时效)
                logger.info(
                    f"wechatpay.order.reuse user_id={user.user_id} plan={plan} "
                    f"order_id={existing.order_id} out_trade_no={existing.out_trade_no}"
                )
                # 重新调 SDK 让 paySign 时间戳新鲜 (旧的可能过期)
                # 注意: 这里调用真 SDK 时 wechatpayv3 会拿原 out_trade_no 调一次
                # ``/v3/pay/transactions/jsapi``, 微信侧返同 prepay_id (UNIQUE 幂等键)
                try:
                    payment_params = await client.create_jsapi_order(
                        out_trade_no=existing.out_trade_no,
                        amount_cny=existing.amount_cny,
                        description=description,
                        openid=openid,
                    )
                except WechatPayError as e:
                    raise PaymentError("wechat_pay_error", str(e)) from e
                return CreateOrderResult(
                    order_id=existing.order_id,
                    out_trade_no=existing.out_trade_no,
                    plan=existing.plan,
                    amount_cny=existing.amount_cny,
                    payment_channel=existing.payment_channel,
                    payment_params=payment_params,
                    created_at=existing.created_at,
                )

        # 新建订单
        out_trade_no = _generate_out_trade_no()
        order = VipOrder(
            user_id=user.user_id,
            out_trade_no=out_trade_no,
            plan=plan,
            amount_cny=amount_cny,
            status="pending",
            payment_channel=payment_channel,
        )
        session.add(order)
        await session.flush()  # 拿 order_id / created_at

        # 调 SDK 下单. 失败回滚整个事务 (订单不落库, 用户 retry 时拿新 out_trade_no).
        try:
            payment_params = await client.create_jsapi_order(
                out_trade_no=out_trade_no,
                amount_cny=amount_cny,
                description=description,
                openid=openid,
            )
        except WechatPayError as e:
            logger.warning(
                f"wechatpay.order.create_fail user_id={user.user_id} plan={plan} "
                f"out_trade_no={out_trade_no} err={e!r}"
            )
            raise PaymentError("wechat_pay_error", str(e)) from e

        logger.info(
            f"wechatpay.order.created user_id={user.user_id} plan={plan} "
            f"order_id={order.order_id} out_trade_no={out_trade_no} amount_cny={amount_cny}"
        )
        return CreateOrderResult(
            order_id=order.order_id,
            out_trade_no=out_trade_no,
            plan=plan,
            amount_cny=amount_cny,
            payment_channel=payment_channel,
            payment_params=payment_params,
            created_at=order.created_at,
        )


# ─── 2. 回调 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CallbackResult:
    """``handle_wechat_callback`` 返回, 路由层组 ``NotifyResponse``."""

    code: str  # SUCCESS / FAIL
    message: str


async def handle_wechat_callback(
    *,
    headers: dict[str, str],
    body: bytes,
) -> CallbackResult:
    """处理微信支付回调 (POST /pay/wechat/notify).

    步骤:
    1. SDK 验签 + 解密 → ``CallbackPayload`` 或 None
    2. 验签失败 → 返 FAIL (微信会重试, 但 Stub 模式下不重试; 真模式下应人工介入)
    3. 反查订单; 不存在 → 返 SUCCESS (孤儿回调, 拒收让微信不要再发)
    4. 已 ``status='paid'`` 且 ``transaction_id`` 一致 → 幂等命中, 返 SUCCESS
    5. ``trade_state != SUCCESS`` → 落 ``status='failed'`` 或 ``refunded``, 返 SUCCESS
    6. 金额校验: ``amount_total_cents != amount_cny * 100`` → 严重 log, 返 FAIL
    7. ``trade_state == SUCCESS``: 落 ``status='paid'`` + 调 ``vip_service.apply_paid_order``
    """
    client = get_client_for_request()
    payload: CallbackPayload | None = await client.verify_and_decrypt_callback(
        headers=headers, body=body
    )
    if payload is None:
        logger.warning("wechatpay.callback.verify_failed")
        return CallbackResult(code="FAIL", message="signature verify failed")

    factory = get_session_factory()
    async with factory() as session, session.begin():
        order = (
            await session.execute(
                select(VipOrder).where(VipOrder.out_trade_no == payload.out_trade_no)
            )
        ).scalar_one_or_none()
        if order is None:
            logger.warning(
                f"wechatpay.callback.order_not_found out_trade_no={payload.out_trade_no} "
                f"transaction_id={payload.transaction_id} (treating as orphan, ack SUCCESS)"
            )
            return CallbackResult(code="SUCCESS", message="OK")

        # 幂等: 同 transaction_id 已处理 → 直接 SUCCESS
        if (
            order.status == "paid"
            and order.transaction_id == payload.transaction_id
        ):
            logger.info(
                f"wechatpay.callback.idempotent order_id={order.order_id} "
                f"transaction_id={payload.transaction_id}"
            )
            return CallbackResult(code="SUCCESS", message="OK")

        # 非 SUCCESS state → 标 failed (允许用户重试)
        if payload.trade_state != "SUCCESS":
            order.status = "failed"
            order.transaction_id = payload.transaction_id
            order.raw_callback = _serialize_raw(payload.raw)
            logger.warning(
                f"wechatpay.callback.non_success order_id={order.order_id} "
                f"trade_state={payload.trade_state} (marked failed)"
            )
            return CallbackResult(code="SUCCESS", message="OK")

        # 金额校验 (核心防漏洞)
        expected_cents = int((order.amount_cny * 100).quantize(Decimal("1")))
        if payload.amount_total_cents != expected_cents:
            logger.error(
                f"wechatpay.callback.amount_mismatch order_id={order.order_id} "
                f"expected_cents={expected_cents} got_cents={payload.amount_total_cents} "
                f"transaction_id={payload.transaction_id} "
                f"(NOT marking paid; manual intervention required)"
            )
            return CallbackResult(
                code="FAIL", message=f"amount mismatch: expected {expected_cents} cents"
            )

        # 落 paid + 流转 membership
        order.status = "paid"
        order.transaction_id = payload.transaction_id
        order.paid_at = payload.success_time or datetime.now(UTC)
        order.raw_callback = _serialize_raw(payload.raw)
        await session.flush()

        await vip_service.apply_paid_order(
            session, user_id=order.user_id, order=order
        )

        logger.info(
            f"wechatpay.callback.paid order_id={order.order_id} "
            f"transaction_id={payload.transaction_id} amount_cny={order.amount_cny} "
            f"plan={order.plan}"
        )
        return CallbackResult(code="SUCCESS", message="OK")


def _serialize_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """把回调原始 dict 里的非 JSON 类型 (datetime 之类) 转成 string, 确保 JSONB 落库不炸.

    简单走 ``json.loads(json.dumps(default=str))`` 兜底. 对 raw 体积不敏感
    (单笔 ~ 1.5KB), 不优化.
    """
    safe: dict[str, Any] = json.loads(json.dumps(raw, default=str, ensure_ascii=False))
    return safe


__all__ = [
    "CallbackResult",
    "CreateOrderResult",
    "PLAN_PRICES_CNY",
    "PaymentError",
    "create_wechat_jsapi_order",
    "handle_wechat_callback",
]
