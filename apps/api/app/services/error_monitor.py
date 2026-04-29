"""OPS-S4-001 + OPS-S5-002 错误率监控 + 告警.

定位 (S4 起步)
==============
- spec/07 §S4 灰度上线前必须有 "错误率 > 1% 触发告警" 的最低保障. 本模块:
  1. 统计每分钟 5xx + unhandled exception 占比 (走 Redis 滑动窗 ZSET)
  2. 越阈值时打 ERROR 日志 (CI / loguru 可见) + 调钉钉 webhook (生产可见)
  3. 提供 admin GET 查最近窗口的 total / error / pct, 支持 Bad Case 跟踪面板

S5 增量 (本 PR OPS-S5-002)
==========================
- 告警字段标准化: 钉钉 ``markdown`` 类型, 含 severity / env / error_pct / window /
  module / hostname / runbook / @user. 与 OPS-S5-001 Sentry trace ID 互补 — 钉钉
  做"立刻看, 谁值班"; Sentry 做"事后翻栈".
- 钉钉机器人加签 (HMAC-SHA256) 模式: ``ALERT_DINGTALK_SECRET`` 配置即启用,
  否则关键词模式 (告警内容必含 ``XGZH-ALERT``).
- runbook 链接: ``ALERT_RUNBOOK_BASE_URL`` 配置后, markdown 里附 ``runbook`` 字段
  + 钉钉 markdown 直接渲染为可点击链接.

为什么不直接接 Sentry 取代本模块
==============================
- Sentry 做"事后调用栈分析"是擅长的, 但**实时阈值告警 + 关键人员 @ 触达**走 Sentry
  webhook 的话依赖第三方平台规则, 本地 / CI / 私有部署都不可控.
- error_monitor 定位"独立运行 + 不依赖外部 SaaS"的最小告警链路, 与 OPS-S5-001
  Sentry SDK 共存且分工明确: error_monitor = 实时告警, Sentry = 事后 trace.

为什么用 Redis 而不是 in-process 计数器
=====================================
- 多 worker (uvicorn workers / gunicorn 多进程) 内存计数会拆 N 份, 阈值判断不准.
- Redis 单线程 ZSET ZADD 原子, 跨 worker 计数自然合并.

字段
----
- ``counters:requests`` ZSET: 每条请求一条 ``(now_ms, request_id)``
- ``counters:errors``   ZSET: 每条 5xx / unhandled exception 一条
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import socket
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from loguru import logger

from app.cache import get_redis_client
from app.core.config import Settings, get_settings

REQUESTS_KEY = "ops:metrics:requests"  # → xgzh:ops:metrics:requests (ZSET)
ERRORS_KEY = "ops:metrics:errors"
ALERT_LATCH_KEY = "ops:metrics:alert_latched"  # 告警 latch 标志, 防 N 秒内反复发同条

_ALERT_LATCH_TTL_SECONDS = 60  # 同一阈值告警 60s 内不重复 (避免风暴)
_ALERT_NAME = "error_rate_high"
_DINGTALK_KEYWORD = "XGZH-ALERT"  # 关键词模式必须出现的字符串

Severity = Literal["P0", "P1", "P2"]


@dataclass(frozen=True, slots=True)
class ErrorMetrics:
    window_seconds: int
    total_requests: int
    total_errors: int
    error_pct: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_seconds": self.window_seconds,
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "error_pct": round(self.error_pct, 3),
        }


# ─── 滑动窗记录 / 读取 ────────────────────────────────────────────


async def record_request(*, request_id: str, is_error: bool) -> None:
    """每条 HTTP 请求结束时调一次. ``is_error`` = 5xx 或 unhandled exception.

    错误处理: redis 故障时 fail-soft (warn + 返回), 不影响业务请求成功. 告警丢失
    比业务挂掉划算; 多 worker 间允许偶尔漏统计."""
    settings = get_settings()
    window = settings.error_alert_window_seconds
    now_ms = int(time.time() * 1000)
    client = get_redis_client()
    try:
        await client.sliding_window_record(
            REQUESTS_KEY,
            window_seconds=window,
            member=request_id,
            now_ms=now_ms,
        )
        if is_error:
            await client.sliding_window_record(
                ERRORS_KEY,
                window_seconds=window,
                member=request_id,
                now_ms=now_ms,
            )
            await _maybe_alert(now_ms=now_ms)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"error_monitor.record_request_failed: {e}")


async def get_metrics() -> ErrorMetrics:
    """读最近 ``error_alert_window_seconds`` 内的 total / errors / pct."""
    settings = get_settings()
    window = settings.error_alert_window_seconds
    now_ms = int(time.time() * 1000)
    client = get_redis_client()
    total = await client.sliding_window_count(
        REQUESTS_KEY, window_seconds=window, now_ms=now_ms
    )
    errors = await client.sliding_window_count(
        ERRORS_KEY, window_seconds=window, now_ms=now_ms
    )
    pct = (errors / total * 100.0) if total > 0 else 0.0
    return ErrorMetrics(
        window_seconds=window,
        total_requests=total,
        total_errors=errors,
        error_pct=pct,
    )


async def reset_metrics() -> None:
    """admin/debug: 清当前窗口内的所有请求 / 错误计数 + latch."""
    client = get_redis_client()
    await client.delete(REQUESTS_KEY)
    await client.delete(ERRORS_KEY)
    await client.delete(ALERT_LATCH_KEY)


# ─── 告警字段标准化 (OPS-S5-002) ──────────────────────────────────


def derive_severity(error_pct: float, threshold_pct: float) -> Severity:
    """按 error_pct 决定告警严重级.

    - ≥ 5%      → P0 (业务大概率不可用, oncall 立即介入)
    - ≥ 2 *threshold (阈值的 2 倍) → P1
    - 其他超阈值 → P2

    threshold_pct 默认 1%, 即 ≥ 5% 是 P0, ≥ 2% 是 P1, ≥ 1% 是 P2.
    """
    if error_pct >= 5.0:
        return "P0"
    if error_pct >= max(threshold_pct * 2.0, 2.0):
        return "P1"
    return "P2"


def build_alert_payload(
    *,
    metrics: ErrorMetrics,
    settings: Settings,
    hostname: str | None = None,
) -> dict[str, Any]:
    """造钉钉 markdown payload (含 ``XGZH-ALERT`` 关键词 + ``@`` 列表 + runbook).

    抽出来是为了让单测可以离线断言字段齐全 + 不用真打钉钉. ``hostname`` 留 None
    时走 ``socket.gethostname()`` (生产真主机名).

    钉钉 markdown 协议: spec/07 §S5 给的格式 — title 在 push 通知列表显示, text
    用 ``\\n`` 而非 ``<br>`` (钉钉 markdown 完全兼容标准 md 子集).
    """
    severity = derive_severity(metrics.error_pct, settings.error_alert_threshold_pct)
    env = settings.app_env
    module = settings.alert_module_name
    host = hostname if hostname is not None else socket.gethostname()

    # markdown 正文 — 与 spec/12 §OPS-S5-002 给的格式一一对应
    lines = [
        f"## ⚠️ {_DINGTALK_KEYWORD} ERROR RATE HIGH",
        "",
        f"- **severity**: {severity}",
        f"- **env**: {env}",
        (
            f"- **error_pct**: {metrics.error_pct:.2f}% "
            f"(above threshold {settings.error_alert_threshold_pct:g}%)"
        ),
        (
            f"- **window**: {metrics.window_seconds}s "
            f"samples={metrics.total_requests} errors={metrics.total_errors}"
        ),
        f"- **module**: {module}",
        f"- **hostname**: {host}",
    ]
    if settings.alert_runbook_base_url.strip():
        runbook_url = (
            settings.alert_runbook_base_url.rstrip("/") + "/" + _ALERT_NAME
        )
        lines.append(f"- **runbook**: [{runbook_url}]({runbook_url})")

    # @ 提及 — 钉钉 markdown 里 ``@手机号`` / ``@userId`` 必须**同时**出现在 text body
    # 和 ``at`` 字段, 否则只会发到所有人的群但不触达指定人通知
    at_user_ids = _split_csv(settings.alert_at_user_ids)
    at_mobiles = _split_csv(settings.alert_at_mobiles)
    if at_user_ids or at_mobiles:
        mention_inline = " ".join(
            [f"@{uid}" for uid in at_user_ids] + [f"@{m}" for m in at_mobiles]
        )
        lines.append("")
        lines.append(mention_inline)

    text = "\n".join(lines)
    payload: dict[str, Any] = {
        "msgtype": "markdown",
        "markdown": {
            "title": f"{_DINGTALK_KEYWORD} {severity} {env}",
            "text": text,
        },
    }
    # 钉钉机器人 atUserIds 是 V2 推荐字段; isAtAll 显式 false 防 V1 误读
    if at_user_ids or at_mobiles:
        payload["at"] = {
            "atUserIds": at_user_ids,
            "atMobiles": at_mobiles,
            "isAtAll": False,
        }
    return payload


def sign_dingtalk_url(webhook: str, secret: str, *, now_ms: int | None = None) -> str:
    """钉钉机器人加签 URL.

    算法 (钉钉官方文档):
        timestamp = current ms
        sign_str  = "{timestamp}\\n{secret}"
        hmac_code = HMAC_SHA256(secret_bytes, sign_str_bytes)
        sign      = url_quote(base64(hmac_code))
        url       = "{webhook}&timestamp={timestamp}&sign={sign}"

    secret 为空时直接返原 URL (关键词模式由调用方保证内容含 ``XGZH-ALERT``).
    ``now_ms`` 可注入, 让单测能锁定时间断言 sign 字面量.
    """
    if not secret:
        return webhook
    ts = now_ms if now_ms is not None else int(time.time() * 1000)
    sign_str = f"{ts}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    sep = "&" if "?" in webhook else "?"
    return f"{webhook}{sep}timestamp={ts}&sign={sign}"


def _split_csv(s: str) -> list[str]:
    return [item.strip() for item in s.split(",") if item.strip()]


async def send_dingtalk(payload: dict[str, Any], *, settings: Settings | None = None) -> bool:
    """发钉钉告警, 返 ``True`` 已发, ``False`` 跳过 / 失败. fail-soft.

    抽成独立 async 函数让外部 (如人工 ``runbook test alert``) 可以直接调.
    """
    cfg = settings or get_settings()
    webhook = cfg.alert_dingtalk_webhook.strip()
    if not webhook:
        return False
    url = sign_dingtalk_url(webhook, cfg.alert_dingtalk_secret.strip())
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.post(url, json=payload)
            if resp.status_code >= 400:
                logger.warning(
                    f"error_monitor.dingtalk_http_status={resp.status_code} "
                    f"body={resp.text[:200]!r}"
                )
                return False
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"error_monitor.dingtalk_failed: {e}")
        return False


async def _maybe_alert(*, now_ms: int) -> None:
    """超阈值时打 ERROR 日志 + 调钉钉.

    Latch 机制: 触发后 ``_ALERT_LATCH_TTL_SECONDS`` 内不再重复告警, 避免 1 min 内
    千次错误把钉钉刷屏. latch 走 Redis ``set ... ex=60``, 跨 worker 共享.
    """
    settings = get_settings()
    threshold = settings.error_alert_threshold_pct
    if threshold <= 0:
        return  # 0 = 关告警

    metrics = await get_metrics()
    # 样本太少时不告警 (10 内出 1 个就 10% 触阈, 噪音太大)
    if metrics.total_requests < 20:
        return
    if metrics.error_pct < threshold:
        return

    client = get_redis_client()
    latched = await client.get(ALERT_LATCH_KEY)
    if latched is not None:
        return
    await client.set(ALERT_LATCH_KEY, str(now_ms), ttl_seconds=_ALERT_LATCH_TTL_SECONDS)

    payload = build_alert_payload(metrics=metrics, settings=settings)
    # ERROR 日志 (loguru 显式记一行 + 关键词便于 grep, 即使钉钉不通也有 trail)
    logger.error(
        f"[{_DINGTALK_KEYWORD}] severity={derive_severity(metrics.error_pct, threshold)} "
        f"error_pct={metrics.error_pct:.2f}% "
        f"({metrics.total_errors}/{metrics.total_requests}) "
        f"window={metrics.window_seconds}s threshold={threshold}%"
    )
    await send_dingtalk(payload, settings=settings)


__all__ = [
    "ErrorMetrics",
    "Severity",
    "build_alert_payload",
    "derive_severity",
    "get_metrics",
    "record_request",
    "reset_metrics",
    "send_dingtalk",
    "sign_dingtalk_url",
]


def metrics_payload(metrics: ErrorMetrics) -> str:
    """``json.dumps`` 包装, 给 admin 路由 / 测试断言用."""
    return json.dumps(metrics.as_dict(), ensure_ascii=False)
