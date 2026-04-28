"""OPS-S4-001 错误率监控 + 告警.

定位:
- spec/07 §S4 灰度上线前必须有"错误率 > 1% 触发告警"的最低保障. 本模块:
  1. 统计每分钟 5xx + unhandled exception 占比 (走 Redis 滑动窗 ZSET)
  2. 越阈值时打 ERROR 日志 (CI / loguru 可见) + 调钉钉 webhook (生产可见)
  3. 提供 admin GET 查最近窗口的 total / error / pct, 支持 Bad Case 跟踪面板

为什么不直接接 Sentry:
- Sentry SDK 需要 DSN + 流量上报权限, 目前还没采购 / 配置, 留 OPS-S4 后续 sprint 接.
  本模块定位"独立运行 + 不依赖外部 SaaS"的最小告警链路, Sentry 接进来后仍可叠加.

为什么用 Redis 而不是 in-process 计数器:
- 多 worker (uvicorn workers / gunicorn 多进程) 内存计数会拆 N 份, 阈值判断不准.
- Redis 单线程 ZSET ZADD 原子, 跨 worker 计数自然合并.

字段:
- ``counters:requests`` ZSET: 每条请求一条 ``(now_ms, request_id)``
- ``counters:errors``   ZSET: 每条 5xx / unhandled exception 一条
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from app.cache import get_redis_client
from app.core.config import get_settings

REQUESTS_KEY = "ops:metrics:requests"  # → xgzh:ops:metrics:requests (ZSET)
ERRORS_KEY = "ops:metrics:errors"
ALERT_LATCH_KEY = "ops:metrics:alert_latched"  # 告警 latch 标志, 防 N 秒内反复发同条

_ALERT_LATCH_TTL_SECONDS = 60  # 同一阈值告警 60s 内不重复 (避免风暴)


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


async def _maybe_alert(*, now_ms: int) -> None:
    """超阈值时打 ERROR 日志 + 钉钉 webhook (mock-friendly).

    Latch 机制: 触发后 ``_ALERT_LATCH_TTL_SECONDS`` 内不再重复告警, 避免 1 min 内
    千次错误把钉钉刷屏. latch 走 Redis ``set ... ex=60``, 跨 worker 共享."""
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

    body = (
        f"[XGZH-ALERT] error_rate={metrics.error_pct:.2f}% "
        f"({metrics.total_errors}/{metrics.total_requests}) "
        f"window={metrics.window_seconds}s threshold={threshold}%"
    )
    logger.error(body)

    webhook = settings.alert_dingtalk_webhook.strip()
    if not webhook:
        # dev / CI 默认: 只 log, 不真发
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            await http.post(
                webhook,
                json={
                    "msgtype": "text",
                    "text": {"content": body},
                },
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"error_monitor.dingtalk_failed: {e}")


async def reset_metrics() -> None:
    """admin/debug: 清当前窗口内的所有请求 / 错误计数 + latch."""
    client = get_redis_client()
    await client.delete(REQUESTS_KEY)
    await client.delete(ERRORS_KEY)
    await client.delete(ALERT_LATCH_KEY)


__all__ = [
    "ErrorMetrics",
    "get_metrics",
    "record_request",
    "reset_metrics",
]


def metrics_payload(metrics: ErrorMetrics) -> str:
    """``json.dumps`` 包装, 给 admin 路由 / 测试断言用."""
    return json.dumps(metrics.as_dict(), ensure_ascii=False)
