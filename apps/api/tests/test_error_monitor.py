"""OPS-S4-001 error_monitor 单元测."""

from __future__ import annotations

import pytest

from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.core.config import get_settings
from app.services import error_monitor


@pytest.fixture(autouse=True)
def _redis_client(monkeypatch: pytest.MonkeyPatch) -> InMemoryRedisClient:
    client = InMemoryRedisClient()
    set_redis_client(client)
    # 阈值统一打小, 让 1% 在测试样本下就能触发 / 不触发都可控
    monkeypatch.setenv("ERROR_ALERT_THRESHOLD_PCT", "5.0")
    monkeypatch.setenv("ERROR_ALERT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("ALERT_DINGTALK_WEBHOOK", "")  # log-only
    get_settings.cache_clear()
    yield client
    reset_redis_client()


@pytest.mark.asyncio
async def test_initial_metrics_zero() -> None:
    metrics = await error_monitor.get_metrics()
    assert metrics.total_requests == 0
    assert metrics.total_errors == 0
    assert metrics.error_pct == 0.0


@pytest.mark.asyncio
async def test_record_only_success_keeps_pct_zero() -> None:
    for i in range(10):
        await error_monitor.record_request(request_id=f"r-{i}", is_error=False)
    metrics = await error_monitor.get_metrics()
    assert metrics.total_requests == 10
    assert metrics.total_errors == 0
    assert metrics.error_pct == 0.0


@pytest.mark.asyncio
async def test_record_mixed_success_and_error() -> None:
    for i in range(95):
        await error_monitor.record_request(request_id=f"ok-{i}", is_error=False)
    for i in range(5):
        await error_monitor.record_request(request_id=f"err-{i}", is_error=True)
    metrics = await error_monitor.get_metrics()
    assert metrics.total_requests == 100
    assert metrics.total_errors == 5
    assert metrics.error_pct == 5.0


@pytest.mark.asyncio
async def test_alert_threshold_under_does_not_latch(caplog: pytest.LogCaptureFixture) -> None:
    """低于阈值不告警, latch 不打."""
    for i in range(100):
        await error_monitor.record_request(request_id=f"ok-{i}", is_error=False)
    # 4 / 100 = 4% < 5% 阈值
    for i in range(4):
        await error_monitor.record_request(request_id=f"err-{i}", is_error=True)

    from app.cache import get_redis_client
    client = get_redis_client()
    latched = await client.get(error_monitor.ALERT_LATCH_KEY)
    assert latched is None


@pytest.mark.asyncio
async def test_alert_threshold_over_triggers_latch() -> None:
    """超阈值要触发告警 latch (logger.error 已打 + latch 已置位)."""
    for i in range(95):
        await error_monitor.record_request(request_id=f"ok-{i}", is_error=False)
    # 6 / 101 = ~5.94% > 5%
    for i in range(6):
        await error_monitor.record_request(request_id=f"err-{i}", is_error=True)

    from app.cache import get_redis_client
    client = get_redis_client()
    latched = await client.get(error_monitor.ALERT_LATCH_KEY)
    assert latched is not None


@pytest.mark.asyncio
async def test_alert_skipped_when_sample_too_small() -> None:
    """样本 < 20 时不告警, 即便错误率 100% (单条错误就 100% 告警噪音太大)."""
    for i in range(5):
        await error_monitor.record_request(request_id=f"err-{i}", is_error=True)
    from app.cache import get_redis_client
    client = get_redis_client()
    latched = await client.get(error_monitor.ALERT_LATCH_KEY)
    assert latched is None


@pytest.mark.asyncio
async def test_reset_metrics_clears_all() -> None:
    for i in range(10):
        await error_monitor.record_request(request_id=f"ok-{i}", is_error=i < 3)
    await error_monitor.reset_metrics()
    metrics = await error_monitor.get_metrics()
    assert metrics.total_requests == 0
    assert metrics.total_errors == 0


@pytest.mark.asyncio
async def test_4xx_not_counted_as_error() -> None:
    """spec/07 §S4: 4xx (鉴权失败 / 参数错) 是用户行为, 不计入错误率.

    本测验调用方传 ``is_error=False`` 时 (即便 status=4xx) 不计 error;
    main.py 中间件已经只 ``is_error = status >= 500``."""
    for i in range(50):
        await error_monitor.record_request(request_id=f"4xx-{i}", is_error=False)
    metrics = await error_monitor.get_metrics()
    assert metrics.total_requests == 50
    assert metrics.total_errors == 0
    assert metrics.error_pct == 0.0
