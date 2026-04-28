"""微信支付 v3 域 Pydantic 模型 (BE-S3-010).

下单 / 回调请求 / 响应 4 类:

- ``CreateOrderRequest``    ← ``POST /pay/wechat/order``  请求体
- ``CreateOrderResponse``   ← ``POST /pay/wechat/order``  响应
- ``PaymentParams``         ← ``CreateOrderResponse.payment_params`` 内嵌; 5 件套字段 100% 对齐
  ``uni.requestPayment`` 入参 (前端不做字段映射)
- ``NotifyResponse``        ← ``POST /pay/wechat/notify`` 响应; 微信 v3 协议固定结构

错误响应统一走 ``HTTPException(detail={'code': '...', 'message': '...'})``,
不在 schema 层定义错误模型 (与 auth / agent 模块一致).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 仅接受真实付费档; 试用走 vip_service.grant_trial 不走此路径
PayablePlanLiteral = Literal["monthly", "quarterly", "yearly", "lifetime"]
PaymentChannelLiteral = Literal["wechat_mp", "wechat_h5"]


class CreateOrderRequest(BaseModel):
    """``POST /pay/wechat/order`` 请求体.

    - ``plan``: 必填; 仅 4 种付费档. 服务端自带价目表算金额, 前端不传 amount (防篡改).
    - ``payment_channel``: 默认 ``wechat_mp`` (小程序 JSAPI). H5 / Native 走不同 SDK 调用.
    """

    model_config = ConfigDict(extra="forbid")

    plan: PayablePlanLiteral
    payment_channel: PaymentChannelLiteral = Field(
        default="wechat_mp",
        description="支付渠道; 当前仅支持 wechat_mp (小程序内 JSAPI). wechat_h5 预留 Sprint 4+",
    )


class PaymentParams(BaseModel):
    """``uni.requestPayment`` 调用入参 5 件套, 微信小程序 JSAPI 协议固定.

    与 ``wx.requestPayment`` 1:1 对齐, 字段名严格用驼峰 (微信协议要求, 前端零字段映射).

    N815 mixedCase 是微信硬协议要求 (``wx.requestPayment`` 入参字段名固定),
    本项目其它地方走 snake_case; 这里独立例外, 走 noqa.
    """

    model_config = ConfigDict(extra="forbid")

    timeStamp: str = Field(  # noqa: N815  WeChat Pay JSAPI protocol field name
        description="Unix 秒时间戳字符串 (10 位); 与 paySign 计算输入一致"
    )
    nonceStr: str = Field(  # noqa: N815  WeChat Pay JSAPI protocol field name
        description="随机串 (≤ 32 位); 与 paySign 计算输入一致"
    )
    package: str = Field(description="prepay_id 包装串, 格式 'prepay_id=wx...'")
    signType: Literal["RSA"] = Field(  # noqa: N815  WeChat Pay JSAPI protocol field name
        default="RSA", description="v3 协议固定 RSA-SHA256"
    )
    paySign: str = Field(  # noqa: N815  WeChat Pay JSAPI protocol field name
        description="商户私钥签名结果 (Base64); 微信侧用商户公钥验签后授权拉起支付"
    )


class CreateOrderResponse(BaseModel):
    """``POST /pay/wechat/order`` 响应.

    - ``order_id`` / ``out_trade_no``: 内部业务键 + 商户订单号; 前端持久化用
    - ``amount_cny``: 计价确认 (前端展示用; 与请求 plan 对应的服务端价目表一致)
    - ``payment_params``: 直接喂给 ``uni.requestPayment``, 前端无需做任何变换
    """

    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    out_trade_no: str
    plan: PayablePlanLiteral
    amount_cny: Decimal
    payment_channel: PaymentChannelLiteral
    payment_params: PaymentParams
    created_at: datetime


class NotifyResponse(BaseModel):
    """``POST /pay/wechat/notify`` 响应; 微信 v3 协议固定结构.

    协议要求即使业务失败也返回 HTTP 200 + body ``{code: 'FAIL', message: '...'}``;
    返非 200 / 非 SUCCESS 时微信会持续重试 24h (按指数退避), 容易把订单状态搞乱.

    成功固定: ``{code: 'SUCCESS', message: 'OK'}``.
    """

    model_config = ConfigDict(extra="forbid")

    code: Literal["SUCCESS", "FAIL"]
    message: str = Field(default="OK", max_length=128)


__all__ = [
    "CreateOrderRequest",
    "CreateOrderResponse",
    "NotifyResponse",
    "PayablePlanLiteral",
    "PaymentChannelLiteral",
    "PaymentParams",
]
