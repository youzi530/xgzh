"""BE-S3-010 微信支付客户端单元测.

测 ``StubWechatPayClient`` + ``get_wechat_client`` 工厂决策, 不打网络, 不依赖 DB.

注意: ``RealWechatPayClient`` 走真 SDK + 商户私钥, 需要真凭证才能构造; 单测层不
覆盖 (放 e2e 接沙箱测时走 spike). 本文件只覆盖 Stub 层 + 工厂决策, 保证 dev / CI
路径稳态.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.services.payment.wechat_client import (
    StubWechatPayClient,
    WechatPayError,
    get_wechat_client,
)

pytestmark = pytest.mark.asyncio


# ─── StubWechatPayClient.create_jsapi_order ──────────────────────────────


async def test_stub_create_jsapi_order_returns_5_field_payment_params() -> None:
    client = StubWechatPayClient()
    params = await client.create_jsapi_order(
        out_trade_no="XGZH20260101000000ABCDEF",
        amount_cny=Decimal("39.00"),
        description="新股智汇 VIP 月度订阅",
        openid="oFakeOpenIdForUnitTest",
    )
    # 5 件套字段都得有
    assert params.timeStamp.isdigit() and len(params.timeStamp) == 10
    assert len(params.nonceStr) >= 8
    assert params.package.startswith("prepay_id=stub_")
    assert params.signType == "RSA"
    assert len(params.paySign) >= 200  # 模拟 RSA-SHA256 签名长度


async def test_stub_create_jsapi_order_rejects_zero_amount() -> None:
    client = StubWechatPayClient()
    with pytest.raises(WechatPayError, match="invalid amount"):
        await client.create_jsapi_order(
            out_trade_no="XGZH20260101000000ABCDEF",
            amount_cny=Decimal("0"),
            description="x",
            openid="oFakeOpenId",
        )


async def test_stub_create_jsapi_order_rejects_negative_amount() -> None:
    client = StubWechatPayClient()
    with pytest.raises(WechatPayError):
        await client.create_jsapi_order(
            out_trade_no="XGZH20260101000000ABCDEF",
            amount_cny=Decimal("-1.00"),
            description="x",
            openid="oFakeOpenId",
        )


async def test_stub_create_jsapi_order_distinct_nonce_per_call() -> None:
    """两次调用的 nonceStr / paySign 必须不同 (防签名缓存导致前端 paysign 复用)."""
    client = StubWechatPayClient()
    p1 = await client.create_jsapi_order(
        out_trade_no="XGZH20260101000000AAAAAA",
        amount_cny=Decimal("39.00"),
        description="x",
        openid="oFakeOpenId",
    )
    p2 = await client.create_jsapi_order(
        out_trade_no="XGZH20260101000000BBBBBB",
        amount_cny=Decimal("39.00"),
        description="x",
        openid="oFakeOpenId",
    )
    assert p1.nonceStr != p2.nonceStr
    assert p1.paySign != p2.paySign
    assert p1.package != p2.package  # 不同 prepay_id


# ─── StubWechatPayClient.verify_and_decrypt_callback ──────────────────────


async def test_stub_verify_callback_requires_bypass_header() -> None:
    """Stub 模式: 没传 X-Stub-Sign-Override: bypass 视为验签失败 → None."""
    client = StubWechatPayClient()
    body = json.dumps(
        {
            "out_trade_no": "XGZH...",
            "transaction_id": "wxtxn123",
            "trade_state": "SUCCESS",
            "amount": {"total": 3900},
        }
    ).encode("utf-8")
    result = await client.verify_and_decrypt_callback(headers={}, body=body)
    assert result is None


async def test_stub_verify_callback_rejects_wrong_bypass_value() -> None:
    client = StubWechatPayClient()
    body = json.dumps(
        {
            "out_trade_no": "XGZH...",
            "transaction_id": "wxtxn123",
            "trade_state": "SUCCESS",
            "amount": {"total": 3900},
        }
    ).encode("utf-8")
    result = await client.verify_and_decrypt_callback(
        headers={"X-Stub-Sign-Override": "wrong_value"}, body=body
    )
    assert result is None


async def test_stub_verify_callback_decrypts_success_payload() -> None:
    client = StubWechatPayClient()
    body = json.dumps(
        {
            "out_trade_no": "XGZH20260101000000ABCDEF",
            "transaction_id": "4200001234567890",
            "trade_state": "SUCCESS",
            "amount": {"total": 3900, "currency": "CNY"},
            "payer": {"openid": "oFakeOpenId"},
            "success_time": "2026-04-27T12:00:00+08:00",
        }
    ).encode("utf-8")
    payload = await client.verify_and_decrypt_callback(
        headers={"X-Stub-Sign-Override": "bypass"}, body=body
    )
    assert payload is not None
    assert payload.out_trade_no == "XGZH20260101000000ABCDEF"
    assert payload.transaction_id == "4200001234567890"
    assert payload.trade_state == "SUCCESS"
    assert payload.amount_total_cents == 3900
    assert payload.payer_openid == "oFakeOpenId"
    assert payload.success_time is not None
    assert "out_trade_no" in payload.raw  # raw 也保存


async def test_stub_verify_callback_handles_non_success_state() -> None:
    """trade_state=PAYERROR 也应该走通 (返 payload 不返 None), 让 service 决定怎么处理."""
    client = StubWechatPayClient()
    body = json.dumps(
        {
            "out_trade_no": "XGZH...",
            "transaction_id": "txn",
            "trade_state": "PAYERROR",
            "amount": {"total": 3900},
        }
    ).encode("utf-8")
    payload = await client.verify_and_decrypt_callback(
        headers={"X-Stub-Sign-Override": "bypass"}, body=body
    )
    assert payload is not None
    assert payload.trade_state == "PAYERROR"


async def test_stub_verify_callback_rejects_bad_json() -> None:
    client = StubWechatPayClient()
    body = b"not a valid json {{"
    result = await client.verify_and_decrypt_callback(
        headers={"X-Stub-Sign-Override": "bypass"}, body=body
    )
    assert result is None


async def test_stub_verify_callback_rejects_missing_required_fields() -> None:
    """缺 out_trade_no / transaction_id / amount.total → 返 None."""
    client = StubWechatPayClient()
    body = json.dumps({"trade_state": "SUCCESS"}).encode("utf-8")
    result = await client.verify_and_decrypt_callback(
        headers={"X-Stub-Sign-Override": "bypass"}, body=body
    )
    assert result is None


async def test_stub_verify_callback_header_case_insensitive() -> None:
    """header 名大小写不敏感 (微信真协议都是 PascalCase, 我们 Stub 也照顾用户可能传 lowercase)."""
    client = StubWechatPayClient()
    body = json.dumps(
        {
            "out_trade_no": "XGZH...",
            "transaction_id": "txn",
            "trade_state": "SUCCESS",
            "amount": {"total": 3900},
        }
    ).encode("utf-8")
    payload = await client.verify_and_decrypt_callback(
        headers={"x-stub-sign-override": "bypass"},  # 全小写
        body=body,
    )
    assert payload is not None


# ─── get_wechat_client 工厂 ───────────────────────────────────────────────


async def test_get_wechat_client_returns_stub_in_dev_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dev_mode=True 强制返 Stub, 即使 mch / key 凭证齐全."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    get_wechat_client.cache_clear()
    monkeypatch.setenv("WECHATPAY_DEV_MODE", "true")
    # 哪怕设了凭证, dev_mode=true 也直接 Stub
    monkeypatch.setenv("WECHATPAY_MCH_ID", "1900000001")
    monkeypatch.setenv("WECHATPAY_APIV3_KEY", "x" * 32)
    monkeypatch.setenv("WECHATPAY_CERT_SERIAL_NO", "ABCDEF1234567890")
    monkeypatch.setenv("WECHATPAY_PRIVATE_KEY_PATH", "/nonexistent.pem")
    monkeypatch.setenv("WECHATPAY_NOTIFY_URL", "https://api.test/notify")
    monkeypatch.setenv("WECHATPAY_APP_ID", "wx1234567890abcdef")

    try:
        client = get_wechat_client()
        assert client.is_stub is True
    finally:
        get_settings.cache_clear()
        get_wechat_client.cache_clear()


async def test_get_wechat_client_falls_back_stub_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dev_mode=False 但凭证不齐 → fallback 到 Stub (不抛, fail-open warn)."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    get_wechat_client.cache_clear()
    monkeypatch.setenv("WECHATPAY_DEV_MODE", "false")
    monkeypatch.setenv("WECHATPAY_MCH_ID", "")  # 空 → unconfigured
    try:
        client = get_wechat_client()
        assert client.is_stub is True
    finally:
        get_settings.cache_clear()
        get_wechat_client.cache_clear()
