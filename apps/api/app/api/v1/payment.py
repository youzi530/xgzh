"""支付路由 (BE-S3-010).

- ``POST /pay/wechat/order``  — JSAPI 下单 (auth required)
- ``POST /pay/wechat/notify`` — 微信回调 (无 auth, 走 SDK 验签)

读路径 ``GET /vip/me`` / ``GET /vip/orders`` 在 ``app/api/v1/vip.py``;
本路由专管交易写路径 (会落库 + 调微信网络).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.cache import rate_limit
from app.core.logging import logger
from app.db.models import User
from app.schemas.payment import (
    CreateOrderRequest,
    CreateOrderResponse,
    NotifyResponse,
)
from app.security.deps import get_current_user
from app.services.payment import (
    PaymentError,
    create_wechat_jsapi_order,
    handle_wechat_callback,
)

router = APIRouter(prefix="/pay", tags=["payment"])


_PAYMENT_ERROR_TO_STATUS: dict[str, int] = {
    "invalid_plan": status.HTTP_400_BAD_REQUEST,
    "unsupported_channel": status.HTTP_400_BAD_REQUEST,
    "no_wechat_openid": status.HTTP_400_BAD_REQUEST,
    "wechat_pay_error": status.HTTP_502_BAD_GATEWAY,
    "wechat_pay_unconfigured": status.HTTP_503_SERVICE_UNAVAILABLE,
}


@router.post(
    "/wechat/order",
    response_model=CreateOrderResponse,
    summary="微信支付 JSAPI 下单",
)
@rate_limit(
    times=10,
    per_seconds=60,
    namespace="pay_order",
    # 限流 key = user_id; 防同用户连点刷 vip_orders 表 (每分钟最多 10 单)
    key_func=lambda payload, user, request: f"user:{user.user_id}",
)
async def create_wechat_order(
    payload: CreateOrderRequest,
    user: Annotated[User, Depends(get_current_user)],
    request: Request,
) -> CreateOrderResponse:
    """JSAPI 下单, 返 ``payment_params`` 直接喂前端 ``uni.requestPayment``.

    限流: 10 次/min/IP (rate_limit 装饰器); 防恶意刷 vip_orders 表.

    错误映射:
    - ``invalid_plan`` / ``unsupported_channel`` / ``no_wechat_openid`` → 400
    - ``wechat_pay_error``            → 502 (SDK 网络异常 / 微信 5xx)
    - ``wechat_pay_unconfigured``     → 503 (商户号未配置, 部署问题)
    """
    try:
        result = await create_wechat_jsapi_order(
            user, plan=payload.plan, payment_channel=payload.payment_channel
        )
    except PaymentError as e:
        http_status = _PAYMENT_ERROR_TO_STATUS.get(
            e.code, status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        raise HTTPException(
            status_code=http_status,
            detail={"code": e.code, "message": e.message},
        ) from e

    return CreateOrderResponse(
        order_id=result.order_id,
        out_trade_no=result.out_trade_no,
        plan=result.plan,  # type: ignore[arg-type]
        amount_cny=result.amount_cny,
        payment_channel=result.payment_channel,  # type: ignore[arg-type]
        payment_params=result.payment_params,
        created_at=result.created_at,
    )


@router.post(
    "/wechat/notify",
    response_model=NotifyResponse,
    summary="微信支付回调",
    # 协议要求即使业务失败也返 200; 走 status_code=200 不让 FastAPI 自动重写
    status_code=status.HTTP_200_OK,
)
async def wechat_pay_notify(request: Request) -> NotifyResponse:
    """微信支付回调; 验签 + 解密 + 状态机流转.

    协议关键点:
    - 微信侧重试规则: 非 200 / 非 SUCCESS 持续重试 (15s / 15s / 30s / 3m / 10m / 20m / 30m / 30m / 1h / 1h / 2h / 6h / 6h, 总共 24h)
    - 我们策略: 验签失败 → FAIL (上游会重试; 但 prod 触发往往是攻击 / mch_id 配置错, 让 ops 看到 log)
    - 业务路径成功 / 幂等 / 孤儿 / 非 SUCCESS → 一律 SUCCESS (拒绝重投, 自己处理)
    - 金额不匹配 → FAIL (强报警 + 让微信重试触发人工介入)
    """
    body = await request.body()
    headers = dict(request.headers)
    request_id = headers.get("x-request-id", "")
    if request_id:
        logger.info(
            f"wechatpay.notify.recv body_len={len(body)} "
            f"sig={headers.get('wechatpay-signature', '')[:20]}... "
            f"request_id={request_id}"
        )

    result = await handle_wechat_callback(headers=headers, body=body)
    return NotifyResponse(code=result.code, message=result.message)  # type: ignore[arg-type]


__all__ = ["router"]
