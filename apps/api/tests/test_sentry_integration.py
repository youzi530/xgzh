"""OPS-S5-001 Sentry SDK 单测.

覆盖
====
1. DSN 留空 → ``init_sentry`` 返 False, 不调 ``sentry_sdk.init``
2. DSN 有值 → 调 ``init_sentry``, init kwargs 含 dsn / send_default_pii=False /
   traces=0.1 / profiles=0.1 / before_send 是 ``_scrub_event``
3. ``sentry_environment`` 留空时 fallback 到 ``app_env``
4. ``sentry_release`` 留空时不传 release key (避免误传空字符串覆盖 SDK 默认)
5. PII scrub: ``phone`` / ``wechat_openid`` / ``email`` 等命中字段名时被
   redact 为 "[REDACTED]"
6. PII scrub: 非命中字段保持原样
7. PII scrub: 嵌套 dict / list 也能 redact
8. PII scrub: 大小写无关 (``X-Forwarded-For`` / ``Phone`` 都命中)
9. PII scrub 抛异常时不上抛 (event 原样放行 + warning)
10. ``init_sentry`` init 抛异常时返 False, 不破坏 web 启动 (容错)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from app.core.config import Settings
from app.observability.sentry import (
    _build_init_kwargs,
    _scrub_event,
    init_sentry,
)


def _build_settings(**overrides: Any) -> Settings:
    """造一个 Settings 实例, 默认 dsn / env / 采样率都给定."""
    base = {
        "sentry_dsn": "",
        "sentry_environment": "",
        "sentry_traces_sample_rate": 0.1,
        "sentry_profiles_sample_rate": 0.1,
        "sentry_release": "",
        "app_env": "dev",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ─── 1. DSN 空 → 不初始化 ─────────────────────────────────────────


def test_init_skipped_when_dsn_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """``SENTRY_DSN`` 留空 → 直接返 False, 完全不调 ``sentry_sdk.init``."""
    import sentry_sdk

    init_mock = MagicMock()
    monkeypatch.setattr(sentry_sdk, "init", init_mock)

    settings = _build_settings(sentry_dsn="")
    assert init_sentry(settings) is False
    init_mock.assert_not_called()


# ─── 2. DSN 有值 → 调 init + 关键参数齐全 ───────────────────────────


def test_init_called_with_correct_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """DSN 有值 → 调 init, send_default_pii=False, before_send=_scrub_event."""
    import sentry_sdk

    init_mock = MagicMock()
    monkeypatch.setattr(sentry_sdk, "init", init_mock)

    settings = _build_settings(
        sentry_dsn="https://abc@sentry.example.com/1",
        sentry_environment="staging",
        sentry_traces_sample_rate=0.2,
        sentry_profiles_sample_rate=0.05,
    )
    assert init_sentry(settings) is True
    init_mock.assert_called_once()

    kwargs = init_mock.call_args.kwargs
    assert kwargs["dsn"] == "https://abc@sentry.example.com/1"
    assert kwargs["send_default_pii"] is False
    assert kwargs["traces_sample_rate"] == 0.2
    assert kwargs["profiles_sample_rate"] == 0.05
    assert kwargs["environment"] == "staging"
    assert kwargs["before_send"] is _scrub_event
    assert "release" not in kwargs  # 留空时不传


# ─── 3. environment fallback ─────────────────────────────────────


def test_environment_fallback_to_app_env() -> None:
    settings = _build_settings(
        sentry_dsn="https://abc@sentry.example.com/1",
        sentry_environment="",
        app_env="prod",
    )
    kwargs = _build_init_kwargs(settings)
    assert kwargs["environment"] == "prod"


def test_environment_explicit_overrides_app_env() -> None:
    settings = _build_settings(
        sentry_dsn="https://abc@sentry.example.com/1",
        sentry_environment="canary",
        app_env="prod",
    )
    kwargs = _build_init_kwargs(settings)
    assert kwargs["environment"] == "canary"


# ─── 4. release 留空时不传 ────────────────────────────────────────


def test_release_omitted_when_empty() -> None:
    settings = _build_settings(
        sentry_dsn="https://abc@sentry.example.com/1",
        sentry_release="",
    )
    kwargs = _build_init_kwargs(settings)
    assert "release" not in kwargs


def test_release_passed_when_set() -> None:
    settings = _build_settings(
        sentry_dsn="https://abc@sentry.example.com/1",
        sentry_release="v1.2.3",
    )
    kwargs = _build_init_kwargs(settings)
    assert kwargs["release"] == "v1.2.3"


# ─── 5. PII scrub 顶层 redact ─────────────────────────────────────


def test_scrub_redacts_top_level_pii() -> None:
    event = {
        "user": {
            "phone": "+8613100001111",
            "wechat_openid": "oXyZ-foo",
            "id": "user-uuid-123",
        }
    }
    out = _scrub_event(event)
    assert out["user"]["phone"] == "[REDACTED]"
    assert out["user"]["wechat_openid"] == "[REDACTED]"
    assert out["user"]["id"] == "user-uuid-123"  # id 是业务标识, 非 PII, 保留


# ─── 6. 非命中字段保持原样 ─────────────────────────────────────────


def test_scrub_preserves_non_pii_fields() -> None:
    event = {
        "request": {
            "method": "POST",
            "url": "https://example.com/api/v1/foo",
            "status_code": 500,
        },
        "tags": {"app": "xgzh"},
    }
    out = _scrub_event(event)
    assert out == event  # 完全不变


# ─── 7. 嵌套 dict / list 也能 redact ───────────────────────────────


def test_scrub_handles_nested_structures() -> None:
    event = {
        "extra": {
            "users": [
                {"phone": "+8613100000001", "name": "alice"},
                {"phone": "+8613100000002", "name": "bob"},
            ],
            "metadata": {
                "submitter": {
                    "email": "a@b.com",
                    "wechat_unionid": "u-xxx",
                }
            },
        }
    }
    out = _scrub_event(event)
    assert out["extra"]["users"][0]["phone"] == "[REDACTED]"
    assert out["extra"]["users"][1]["phone"] == "[REDACTED]"
    # name 不在白名单, 保留
    assert out["extra"]["users"][0]["name"] == "alice"
    assert out["extra"]["metadata"]["submitter"]["email"] == "[REDACTED]"
    assert out["extra"]["metadata"]["submitter"]["wechat_unionid"] == "[REDACTED]"


# ─── 8. 大小写无关 ───────────────────────────────────────────────


def test_scrub_is_case_insensitive() -> None:
    event = {
        "request": {
            "headers": {
                "X-Forwarded-For": "203.0.113.1",
                "X-Real-IP": "203.0.113.2",
                "User-Agent": "okhttp/4.0",
                "Phone": "+8613199998888",
            }
        }
    }
    out = _scrub_event(event)
    assert out["request"]["headers"]["X-Forwarded-For"] == "[REDACTED]"
    assert out["request"]["headers"]["X-Real-IP"] == "[REDACTED]"
    assert out["request"]["headers"]["Phone"] == "[REDACTED]"
    assert out["request"]["headers"]["User-Agent"] == "okhttp/4.0"  # 非 PII


# ─── 9. scrub 失败时 fail-soft ───────────────────────────────────


def test_scrub_fail_soft_returns_original_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_walk`` 抛异常 → 不上抛, 原 event 返回 (Sentry 拿到没 redact 的事件
    比 swallow 整个错误更可接受). 验证发出 logger.warning."""

    class _BadDict(dict[str, Any]):
        def items(self) -> Any:
            raise RuntimeError("simulated failure")

    event: dict[str, Any] = {"bad": _BadDict({"phone": "+8613100000001"})}

    warned: list[str] = []

    def fake_warning(msg: str, *args: Any, **kw: Any) -> None:
        warned.append(msg)

    from app.observability import sentry as sentry_mod

    monkeypatch.setattr(sentry_mod.logger, "warning", fake_warning)
    out = _scrub_event(event)
    assert out is event  # 原对象返回
    assert warned, "scrub 失败时应至少一条 warning"
    assert any("scrub_failed" in w for w in warned)


# ─── 10. init 抛异常时 fail-soft ─────────────────────────────────


def test_init_fail_soft_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """sentry_sdk.init 抛异常 → 返 False, 不阻塞启动 (生产 DSN 错填 / 网络不通)."""
    import sentry_sdk

    def boom(*_args: Any, **_kw: Any) -> None:
        raise RuntimeError("DNS fail or invalid DSN")

    monkeypatch.setattr(sentry_sdk, "init", boom)

    warned: list[str] = []
    from app.observability import sentry as sentry_mod

    monkeypatch.setattr(sentry_mod.logger, "warning", lambda m: warned.append(m))

    settings = _build_settings(sentry_dsn="https://abc@sentry.example.com/1")
    assert init_sentry(settings) is False
    assert any("init_failed" in w for w in warned)


# ─── 11. before_send 是函数引用 (Sentry 调用时不会丢) ────────────


def test_before_send_is_callable() -> None:
    settings = _build_settings(sentry_dsn="https://abc@sentry.example.com/1")
    kwargs = _build_init_kwargs(settings)
    cb = kwargs["before_send"]
    assert callable(cb)
    # 行为锁定: 调用 cb 不抛 + 返 dict
    out = cb({"user": {"phone": "+86131"}})
    assert out["user"]["phone"] == "[REDACTED]"
