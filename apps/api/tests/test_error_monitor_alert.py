"""OPS-S5-002 错误率告警字段标准化 + 钉钉加签 + fail-soft 单测.

覆盖
====
- ``derive_severity`` 三档判定 (P0 / P1 / P2)
- ``build_alert_payload`` markdown 格式 + 关键词 + at 字段
- ``sign_dingtalk_url`` HMAC-SHA256 算法字面量锁定 (与钉钉官方文档对齐)
- ``send_dingtalk`` webhook 留空 / 错误码 / 网络异常的 fail-soft 行为
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import urllib.parse
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.services.error_monitor import (
    ErrorMetrics,
    build_alert_payload,
    derive_severity,
    send_dingtalk,
    sign_dingtalk_url,
)

# ─── 工具 ──────────────────────────────────────────────────────────


def _build_settings(**overrides: Any) -> Settings:
    """造一个 Settings, 给告警相关字段填默认值."""
    base: dict[str, Any] = {
        "app_env": "prod",
        "alert_dingtalk_webhook": "",
        "alert_dingtalk_secret": "",
        "alert_runbook_base_url": "",
        "alert_at_user_ids": "",
        "alert_at_mobiles": "",
        "alert_module_name": "xgzh-api",
        "error_alert_threshold_pct": 1.0,
        "error_alert_window_seconds": 60,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _metrics(error_pct: float, *, total: int = 200, errors: int = 5) -> ErrorMetrics:
    return ErrorMetrics(
        window_seconds=60,
        total_requests=total,
        total_errors=errors,
        error_pct=error_pct,
    )


# ─── 1. derive_severity 三档 ────────────────────────────────────────


@pytest.mark.parametrize(
    "error_pct,expected",
    [
        (10.0, "P0"),
        (5.0, "P0"),  # 边界 5% = P0
        (4.99, "P1"),
        (2.5, "P1"),
        (2.0, "P1"),  # 边界 2% = P1 (默认 threshold=1, 2*1=2)
        (1.99, "P2"),
        (1.0, "P2"),
        (0.5, "P2"),  # 即使低于 threshold 也是 P2 (调用方决定要不要发)
    ],
)
def test_derive_severity_default_threshold(
    error_pct: float, expected: str
) -> None:
    assert derive_severity(error_pct, threshold_pct=1.0) == expected


def test_derive_severity_with_high_threshold() -> None:
    """threshold=3 时 P1 边界 = max(2*3, 2) = 6%; P0 仍硬编码 5% (业务约定:
    P0 = '业务大概率不可用', 与 threshold 无关).
    """
    # 5% 以上一律 P0, 不受 threshold 影响 (P0 = 业务级故障判定)
    assert derive_severity(5.0, threshold_pct=3.0) == "P0"
    # 4.99% < 5% 但仍 ≥ 6%? no, 4.99 < 6, 走 P2
    assert derive_severity(4.99, threshold_pct=3.0) == "P2"
    assert derive_severity(5.99, threshold_pct=3.0) == "P0"  # 5.99 ≥ 5 → P0
    # threshold=0.5 时 P1 边界 = max(2*0.5, 2) = 2 (下限保护)
    assert derive_severity(1.99, threshold_pct=0.5) == "P2"
    assert derive_severity(2.0, threshold_pct=0.5) == "P1"


# ─── 2. build_alert_payload markdown 格式 ─────────────────────────


def test_payload_includes_keyword_and_required_fields() -> None:
    """payload 必含关键词 ``XGZH-ALERT`` (钉钉关键词模式必需) + spec 8 字段."""
    settings = _build_settings(app_env="prod")
    metrics = _metrics(error_pct=2.5, total=200, errors=5)
    payload = build_alert_payload(
        metrics=metrics, settings=settings, hostname="api-prod-1"
    )

    assert payload["msgtype"] == "markdown"
    text = payload["markdown"]["text"]

    # 关键词 (钉钉关键词模式必含)
    assert "XGZH-ALERT" in text
    # 8 字段
    assert "**severity**: P1" in text
    assert "**env**: prod" in text
    assert "**error_pct**: 2.50%" in text
    assert "above threshold 1%" in text
    assert "**window**: 60s" in text
    assert "samples=200" in text
    assert "errors=5" in text
    assert "**module**: xgzh-api" in text
    assert "**hostname**: api-prod-1" in text

    # title 是钉钉 push 通知的简短显示
    title = payload["markdown"]["title"]
    assert "XGZH-ALERT" in title
    assert "P1" in title
    assert "prod" in title


def test_payload_severity_p0_when_above_5pct() -> None:
    settings = _build_settings()
    payload = build_alert_payload(
        metrics=_metrics(error_pct=8.5),
        settings=settings,
        hostname="host",
    )
    assert "**severity**: P0" in payload["markdown"]["text"]
    assert "P0" in payload["markdown"]["title"]


def test_payload_runbook_field_present_when_configured() -> None:
    settings = _build_settings(
        alert_runbook_base_url="https://example.com/wiki/"
    )
    payload = build_alert_payload(
        metrics=_metrics(error_pct=2.0),
        settings=settings,
        hostname="host",
    )
    text = payload["markdown"]["text"]
    # rstrip("/") + "/" + alert_name 应等于 ``https://example.com/wiki/error_rate_high``
    assert "https://example.com/wiki/error_rate_high" in text
    # 同时是可点击的 markdown 链接形式
    assert "[https://example.com/wiki/error_rate_high]" in text


def test_payload_omits_runbook_when_not_configured() -> None:
    settings = _build_settings(alert_runbook_base_url="")
    payload = build_alert_payload(
        metrics=_metrics(error_pct=2.0),
        settings=settings,
        hostname="host",
    )
    assert "runbook" not in payload["markdown"]["text"]


def test_payload_at_field_with_user_ids_and_mobiles() -> None:
    """``at`` 字段填 atUserIds + atMobiles + isAtAll=False;
    text body 同时含 ``@uid`` ``@mobile`` 内联以触发钉钉 push."""
    settings = _build_settings(
        alert_at_user_ids="oncall1, manager02",
        alert_at_mobiles="13800138000",
    )
    payload = build_alert_payload(
        metrics=_metrics(error_pct=2.0),
        settings=settings,
        hostname="host",
    )
    text = payload["markdown"]["text"]

    # at 结构齐全
    at = payload["at"]
    assert at["isAtAll"] is False
    assert at["atUserIds"] == ["oncall1", "manager02"]
    assert at["atMobiles"] == ["13800138000"]

    # text body 内联 @
    assert "@oncall1" in text
    assert "@manager02" in text
    assert "@13800138000" in text


def test_payload_no_at_field_when_neither_configured() -> None:
    settings = _build_settings(alert_at_user_ids="", alert_at_mobiles="")
    payload = build_alert_payload(
        metrics=_metrics(error_pct=2.0),
        settings=settings,
        hostname="host",
    )
    assert "at" not in payload


def test_payload_uses_real_hostname_when_not_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """hostname=None 时走 ``socket.gethostname()`` (生产真主机名)."""
    monkeypatch.setattr("socket.gethostname", lambda: "real-host-007")
    settings = _build_settings()
    payload = build_alert_payload(metrics=_metrics(2.0), settings=settings)
    assert "real-host-007" in payload["markdown"]["text"]


# ─── 3. sign_dingtalk_url 加签 ─────────────────────────────────────


def test_sign_url_returns_original_when_secret_empty() -> None:
    url = "https://oapi.dingtalk.com/robot/send?access_token=xxx"
    assert sign_dingtalk_url(url, "", now_ms=123456) == url


def test_sign_url_with_question_mark_uses_amp() -> None:
    url = "https://oapi.dingtalk.com/robot/send?access_token=xxx"
    signed = sign_dingtalk_url(url, "SECabc", now_ms=1700000000000)
    assert signed.startswith(url + "&")
    assert "timestamp=1700000000000" in signed
    assert "sign=" in signed


def test_sign_url_without_question_mark_uses_qmark() -> None:
    url = "https://example.com/robot"  # no query string
    signed = sign_dingtalk_url(url, "SECabc", now_ms=1700000000000)
    assert signed.startswith(url + "?")
    assert "timestamp=1700000000000" in signed


def test_sign_matches_dingtalk_official_algorithm() -> None:
    """与钉钉官方算法字面量比对; 改算法时锁死."""
    secret = "SECsuperSecret123"
    ts = 1700000000000
    expected_sign_str = f"{ts}\n{secret}"
    expected_hmac = hmac.new(
        secret.encode("utf-8"),
        expected_sign_str.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_sign = urllib.parse.quote_plus(base64.b64encode(expected_hmac))

    signed = sign_dingtalk_url(
        "https://oapi.dingtalk.com/robot/send?access_token=xxx",
        secret,
        now_ms=ts,
    )
    assert f"sign={expected_sign}" in signed


# ─── 4. send_dingtalk fail-soft ──────────────────────────────────


async def test_send_dingtalk_skipped_when_webhook_empty() -> None:
    settings = _build_settings(alert_dingtalk_webhook="")
    payload = build_alert_payload(
        metrics=_metrics(2.0), settings=settings, hostname="h"
    )
    sent = await send_dingtalk(payload, settings=settings)
    assert sent is False


async def test_send_dingtalk_posts_to_signed_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """webhook + secret 都配置 → 真 POST 到 ``url + signed`` 的地址."""
    settings = _build_settings(
        alert_dingtalk_webhook="https://oapi.dingtalk.com/robot/send?access_token=xxx",
        alert_dingtalk_secret="SECabc",
    )
    payload = {"msgtype": "markdown", "markdown": {"title": "t", "text": "x"}}

    captured: dict[str, Any] = {}

    def transport_handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.content
        return httpx.Response(200, json={"errcode": 0})

    transport = httpx.MockTransport(transport_handler)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.services.error_monitor.httpx.AsyncClient", _PatchedClient)

    sent = await send_dingtalk(payload, settings=settings)
    assert sent is True
    assert "timestamp=" in captured["url"]
    assert "sign=" in captured["url"]


async def test_send_dingtalk_returns_false_on_4xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _build_settings(
        alert_dingtalk_webhook="https://oapi.dingtalk.com/robot/send?access_token=xxx",
    )

    def transport_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Bad request")

    transport = httpx.MockTransport(transport_handler)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.services.error_monitor.httpx.AsyncClient", _PatchedClient)

    sent = await send_dingtalk({"a": 1}, settings=settings)
    assert sent is False


async def test_send_dingtalk_returns_false_on_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _build_settings(
        alert_dingtalk_webhook="https://oapi.dingtalk.com/robot/send?access_token=xxx",
    )

    def transport_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated dns fail")

    transport = httpx.MockTransport(transport_handler)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.services.error_monitor.httpx.AsyncClient", _PatchedClient)

    sent = await send_dingtalk({"a": 1}, settings=settings)
    assert sent is False
