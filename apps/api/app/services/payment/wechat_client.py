"""微信支付 v3 SDK 抽象 + 双实现 (BE-S3-010).

抽象意图
========
``payment_service`` 走 ``WechatPayClient`` 接口, 不直接调 wechatpayv3 SDK; 实现层:

- ``RealWechatPayClient``  — 生产; 包 ``wechatpayv3.WeChatPay`` (lazy import 防 dev 环境无 SDK 时炸)
- ``StubWechatPayClient``  — dev / CI / 单测; 不打网络, 返伪 ``payment_params``,
  接受 dev 模拟回调 (``X-Stub-Sign-Override: bypass`` header 直通解密)

接口契约
========
``create_jsapi_order(out_trade_no, amount_cny, description, openid)`` →
  ``PaymentParams`` (timeStamp / nonceStr / package / signType / paySign 5 件套,
  直接喂前端 ``uni.requestPayment``)

``verify_and_decrypt_callback(headers, body)`` → ``CallbackPayload | None``
  - 验签 + 解密 ``resource.ciphertext`` (AES-GCM); 失败返 None
  - 成功返结构化 payload, 关键字段: ``out_trade_no`` / ``transaction_id`` /
    ``trade_state`` / ``amount_total_cents`` / ``payer_openid`` / ``success_time``

工厂
====
``get_wechat_client()`` 走 lru_cache 单例:
- ``settings.wechatpay_dev_mode=True`` 或任意凭证字段空 → ``StubWechatPayClient``
- 否则 → ``RealWechatPayClient`` (lazy import wechatpayv3, 异常时降级 Stub + warn log)
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any, Protocol

from app.core.config import get_settings
from app.core.logging import logger
from app.schemas.payment import PaymentParams


class WechatPayError(Exception):
    """微信支付层错误 (网络 / SDK / 配置缺失). payment_service 层捕获 → 502."""


@dataclass(frozen=True, slots=True)
class CallbackPayload:
    """回调验签 + 解密成功后的结构化 payload.

    与微信原生 ``transaction.success`` 通知报文 1:1 对齐, 但仅暴露 service 用得到的字段.
    其它字段 (sub_appid / sub_mchid / promotion_detail 等) 收纳在 ``raw`` 里.
    """

    out_trade_no: str
    """商户订单号 (与下单时一致, 我们用作幂等键)"""

    transaction_id: str
    """微信支付订单号 (回填 vip_orders.transaction_id)"""

    trade_state: str
    """SUCCESS / REFUND / NOTPAY / CLOSED / REVOKED / USERPAYING / PAYERROR"""

    amount_total_cents: int
    """支付金额 (分); /100 = 元"""

    payer_openid: str | None
    """支付者 openid; H5 / NATIVE 可能为 None"""

    success_time: datetime | None
    """支付完成时间; trade_state=SUCCESS 时填充"""

    raw: dict[str, Any]
    """完整解密后的 dict, 落库 vip_orders.raw_callback 用 (审计)"""


class WechatPayClient(Protocol):
    """微信支付客户端协议."""

    is_stub: bool
    """是否 Stub 实现; payment_service / 测试可断言此字段."""

    async def create_jsapi_order(
        self,
        *,
        out_trade_no: str,
        amount_cny: Decimal,
        description: str,
        openid: str,
    ) -> PaymentParams:
        """JSAPI (小程序 / 公众号) 下单, 返 ``uni.requestPayment`` 5 件套."""
        ...

    async def verify_and_decrypt_callback(
        self, *, headers: dict[str, str], body: bytes
    ) -> CallbackPayload | None:
        """验签 + 解密回调; 失败返 None."""
        ...


# ─── Stub 实现 (dev / CI / 单测) ───────────────────────────────────────────


class StubWechatPayClient:
    """dev / CI / 单测用 Stub. 不打网络, 不验证私钥, 用预设规则模拟微信行为.

    模拟规则
    ========
    - ``create_jsapi_order``: 返伪 ``paySign`` (随机 hex), 字段格式与真协议一致;
      ``package`` 走 ``prepay_id=stub_<8 hex>``, 前端可识别为 dev 单
    - ``verify_and_decrypt_callback``:
      * 校验 header ``X-Stub-Sign-Override: bypass`` —— 没这个 header 视为"伪造回调",
        直接返 None (模拟验签失败)
      * 校验 body 是 JSON, 包含 ``out_trade_no`` + ``transaction_id`` + ``trade_state``
      * 解析后构造 CallbackPayload 直接返
    """

    is_stub = True

    def __init__(self, *, app_id: str = "wxstubappid") -> None:
        self._app_id = app_id

    async def create_jsapi_order(
        self,
        *,
        out_trade_no: str,
        amount_cny: Decimal,
        description: str,
        openid: str,
    ) -> PaymentParams:
        # 仅基本校验, 主要让 service 层逻辑跑通
        if amount_cny <= 0:
            raise WechatPayError(f"invalid amount: {amount_cny}")
        prepay_id = f"stub_{secrets.token_hex(8)}"
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        # 伪 paySign — 真签名走 RSA-SHA256(私钥), Stub 用同样字段拼接的 base64-like 串占位
        # 长度模拟真签名 (~344 chars after base64 of 256-byte RSA result)
        pseudo_sig = secrets.token_urlsafe(256)[:344]
        logger.info(
            f"wechatpay.stub.create_order out_trade_no={out_trade_no} "
            f"amount_cny={amount_cny} openid={openid} prepay_id={prepay_id}"
        )
        return PaymentParams(
            timeStamp=timestamp,
            nonceStr=nonce,
            package=f"prepay_id={prepay_id}",
            signType="RSA",
            paySign=pseudo_sig,
        )

    async def verify_and_decrypt_callback(
        self, *, headers: dict[str, str], body: bytes
    ) -> CallbackPayload | None:
        # Stub 验签: 必须有 X-Stub-Sign-Override: bypass header
        # 大小写不敏感
        sign_override = None
        for k, v in headers.items():
            if k.lower() == "x-stub-sign-override":
                sign_override = v
                break
        if sign_override != "bypass":
            logger.warning(
                "wechatpay.stub.verify_fail missing_or_bad_X-Stub-Sign-Override "
                "(prod 走真验签; dev 必须传 'bypass')"
            )
            return None

        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            logger.warning(f"wechatpay.stub.verify_fail bad_json: {e!r}")
            return None

        out_trade_no = data.get("out_trade_no")
        transaction_id = data.get("transaction_id")
        trade_state = data.get("trade_state", "SUCCESS")
        amount_total = data.get("amount", {}).get("total")
        payer_openid = data.get("payer", {}).get("openid")
        success_time_raw = data.get("success_time")

        if not out_trade_no or not transaction_id or amount_total is None:
            logger.warning(
                f"wechatpay.stub.verify_fail missing_required out_trade_no={out_trade_no!r} "
                f"transaction_id={transaction_id!r} amount_total={amount_total!r}"
            )
            return None

        success_time: datetime | None = None
        if success_time_raw:
            try:
                success_time = datetime.fromisoformat(
                    success_time_raw.replace("Z", "+00:00")
                )
            except ValueError:
                success_time = None

        return CallbackPayload(
            out_trade_no=out_trade_no,
            transaction_id=transaction_id,
            trade_state=trade_state,
            amount_total_cents=int(amount_total),
            payer_openid=payer_openid,
            success_time=success_time,
            raw=data,
        )


# ─── Real 实现 (生产) ────────────────────────────────────────────────────


class RealWechatPayClient:
    """生产用; 包 wechatpayv3.WeChatPay SDK.

    构造时需要凭证齐全 (mch_id / private_key / serial_no / apiv3_key / app_id /
    notify_url); 缺则 raise WechatPayError, 上层 ``get_wechat_client`` 兜底降级 Stub.

    SDK 行为
    --------
    - ``self._wxpay.pay(...)`` 走 v3 ``/v3/pay/transactions/jsapi``, 同步返 ``(http_code, json_str)``
    - ``self._wxpay.callback(headers, body)`` 同步验签 + 解密 (查商户 RSA 公钥 / 平台 X.509);
      内部走 cryptography 库的 ``RSA-PSS-SHA256`` 验签 + AES-GCM 解密
    """

    is_stub = False

    def __init__(
        self,
        *,
        mch_id: str,
        private_key: str,
        cert_serial_no: str,
        apiv3_key: str,
        app_id: str,
        notify_url: str,
    ) -> None:
        try:
            from wechatpayv3 import WeChatPay, WeChatPayType
        except ImportError as e:  # pragma: no cover  # 启动期 fail-fast
            raise WechatPayError("wechatpayv3 SDK not installed") from e

        self._WeChatPayType = WeChatPayType  # noqa: N806
        self._app_id = app_id
        self._notify_url = notify_url
        self._wxpay = WeChatPay(
            wechatpay_type=WeChatPayType.JSAPI,
            mchid=mch_id,
            private_key=private_key,
            cert_serial_no=cert_serial_no,
            appid=app_id,
            apiv3_key=apiv3_key,
            notify_url=notify_url,
        )

    async def create_jsapi_order(
        self,
        *,
        out_trade_no: str,
        amount_cny: Decimal,
        description: str,
        openid: str,
    ) -> PaymentParams:
        # SDK 是同步接口; FastAPI async 上下文走 anyio.to_thread 不阻塞 event loop.
        # wechatpayv3 内部走 requests, 单次调用 ~ 200ms.
        import anyio

        amount_cents = int((amount_cny * 100).quantize(Decimal("1")))
        if amount_cents <= 0:
            raise WechatPayError(f"invalid amount: {amount_cny}")

        def _sync_pay() -> tuple[int, str]:
            result: tuple[int, str] = self._wxpay.pay(
                description=description,
                out_trade_no=out_trade_no,
                amount={"total": amount_cents, "currency": "CNY"},
                payer={"openid": openid},
                pay_type=self._WeChatPayType.JSAPI,
            )
            return result

        try:
            code, message = await anyio.to_thread.run_sync(_sync_pay)
        except Exception as e:  # noqa: BLE001  SDK 包不同 RuntimeError 分支
            logger.exception(f"wechatpay.real.pay_fail out_trade_no={out_trade_no}: {e!r}")
            raise WechatPayError(f"wechatpay sdk pay() raised: {e!r}") from e

        if code != 200:
            logger.warning(
                f"wechatpay.real.pay_non200 out_trade_no={out_trade_no} "
                f"code={code} body={message[:200]}"
            )
            raise WechatPayError(f"wechatpay pay returned http={code} body={message[:200]}")

        try:
            data = json.loads(message)
            prepay_id = data["prepay_id"]
        except (json.JSONDecodeError, KeyError) as e:
            raise WechatPayError(
                f"wechatpay pay returned malformed body={message[:200]}"
            ) from e

        # 计算 paySign: appid\n + timestamp\n + nonce\n + package\n
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        package = f"prepay_id={prepay_id}"
        sign_data = [self._app_id, timestamp, nonce, package]
        try:
            paysign = self._wxpay.sign(sign_data)
        except Exception as e:  # noqa: BLE001
            raise WechatPayError(f"wechatpay sign() raised: {e!r}") from e

        logger.info(
            f"wechatpay.real.create_order out_trade_no={out_trade_no} "
            f"amount_cents={amount_cents} prepay_id={prepay_id}"
        )
        return PaymentParams(
            timeStamp=timestamp,
            nonceStr=nonce,
            package=package,
            signType="RSA",
            paySign=paysign,
        )

    async def verify_and_decrypt_callback(
        self, *, headers: dict[str, str], body: bytes
    ) -> CallbackPayload | None:
        import anyio

        def _sync_callback() -> dict[str, Any] | None:
            data: dict[str, Any] | None = self._wxpay.callback(headers, body)
            return data

        try:
            data = await anyio.to_thread.run_sync(_sync_callback)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"wechatpay.real.callback_raised: {e!r}")
            return None

        if not data:
            logger.warning("wechatpay.real.callback_verify_fail SDK returned None")
            return None

        resource = data.get("resource") or {}
        out_trade_no = resource.get("out_trade_no")
        transaction_id = resource.get("transaction_id")
        trade_state = resource.get("trade_state", "")
        amount = resource.get("amount", {})
        payer = resource.get("payer", {})
        success_time_raw = resource.get("success_time")

        if not out_trade_no or not transaction_id:
            logger.warning(
                f"wechatpay.real.callback_decoded_missing_fields out_trade_no={out_trade_no!r} "
                f"transaction_id={transaction_id!r}"
            )
            return None

        success_time: datetime | None = None
        if success_time_raw:
            try:
                success_time = datetime.fromisoformat(
                    success_time_raw.replace("Z", "+00:00")
                )
            except ValueError:
                success_time = None

        return CallbackPayload(
            out_trade_no=out_trade_no,
            transaction_id=transaction_id,
            trade_state=trade_state,
            amount_total_cents=int(amount.get("total", 0)),
            payer_openid=payer.get("openid"),
            success_time=success_time,
            raw=data,
        )


# ─── 工厂 ────────────────────────────────────────────────────────────────


def _build_real_client() -> WechatPayClient | None:
    """尝试构造真客户端; 缺凭证 / 私钥读取失败 → 返 None 让上层降级."""
    settings = get_settings()
    if not settings.wechatpay_configured:
        return None
    try:
        with open(settings.wechatpay_private_key_path, encoding="utf-8") as f:
            private_key = f.read()
    except OSError as e:
        logger.warning(
            f"wechatpay.private_key_read_fail path={settings.wechatpay_private_key_path} "
            f"err={e!r} (fallback to Stub)"
        )
        return None

    try:
        return RealWechatPayClient(
            mch_id=settings.wechatpay_mch_id,
            private_key=private_key,
            cert_serial_no=settings.wechatpay_cert_serial_no,
            apiv3_key=settings.wechatpay_apiv3_key,
            app_id=settings.wechatpay_app_id,
            notify_url=settings.wechatpay_notify_url,
        )
    except WechatPayError as e:
        logger.warning(f"wechatpay.real_client_init_fail err={e!r} (fallback to Stub)")
        return None


@lru_cache(maxsize=1)
def get_wechat_client() -> WechatPayClient:
    """单例工厂; 走 lru_cache 启动期一次性决定走 Stub vs Real.

    决策树:
    - ``settings.wechatpay_dev_mode=True`` → Stub (强制 dev)
    - ``wechatpay_configured=False`` (任意凭证空) → Stub + warn
    - 私钥读取失败 → Stub + warn
    - 一切正常 → Real

    单测可走 ``get_wechat_client.cache_clear()`` 重置缓存重读 settings.
    """
    settings = get_settings()
    if settings.wechatpay_dev_mode:
        logger.info("wechatpay.client.using_stub (dev_mode=true)")
        return StubWechatPayClient(app_id=settings.wechatpay_app_id or "wxstubappid")

    real = _build_real_client()
    if real is not None:
        logger.info("wechatpay.client.using_real")
        return real

    logger.warning("wechatpay.client.fallback_stub (real init failed)")
    return StubWechatPayClient(app_id=settings.wechatpay_app_id or "wxstubappid")


# 防 mypy 把 WechatPayClient 当成 runtime 不可用 — 运行期它就是个 Protocol class
__all__ = [
    "CallbackPayload",
    "RealWechatPayClient",
    "StubWechatPayClient",
    "WechatPayClient",
    "WechatPayError",
    "get_wechat_client",
]


# 单测专用: 替换全局工厂. 使用模式:
#     from app.services.payment import wechat_client as wc
#     wc._override_client = StubWechatPayClient()
#     ... test ...
#     wc._override_client = None
_override_client: WechatPayClient | None = None


def get_client_for_request() -> WechatPayClient:
    """供 ``payment_service`` 调用; 优先 ``_override_client`` 测试覆盖.

    与 ``get_wechat_client`` 区别: 后者是 lru_cache 全局单例; 前者每次请求都允许测试
    monkey-patch ``_override_client`` 注入 mock client (避免 lru_cache 干扰).
    """
    if _override_client is not None:
        return _override_client
    return get_wechat_client()
