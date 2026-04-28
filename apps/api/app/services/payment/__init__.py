"""支付域服务 (BE-S3-010).

模块结构:
- ``wechat_client`` — 微信支付 v3 SDK 封装; ``WechatPayClient`` 抽象 + 真 / Stub 双实现
- ``payment_service`` — 业务逻辑层; 下单 + 回调验签 + 订阅状态机驱动 (调 vip_service.apply_paid_order)

设计理念
========
- ``payment_service`` 不直接调 wechatpayv3 SDK, 而走 ``WechatPayClient`` 抽象;
  生产 → ``RealWechatPayClient``, 开发 / CI / 单测 → ``StubWechatPayClient``
- 商户私钥 / APIv3 key 等敏感信息只在 ``RealWechatPayClient`` 构造时读取一次,
  service 层零感知 — 测试场景下 ``StubWechatPayClient`` 不需要任何真实凭证
"""

from app.services.payment.payment_service import (
    PLAN_PRICES_CNY,
    PaymentError,
    create_wechat_jsapi_order,
    handle_wechat_callback,
)
from app.services.payment.wechat_client import (
    CallbackPayload,
    StubWechatPayClient,
    WechatPayClient,
    WechatPayError,
    get_wechat_client,
)

__all__ = [
    "CallbackPayload",
    "PLAN_PRICES_CNY",
    "PaymentError",
    "StubWechatPayClient",
    "WechatPayClient",
    "WechatPayError",
    "create_wechat_jsapi_order",
    "get_wechat_client",
    "handle_wechat_callback",
]
