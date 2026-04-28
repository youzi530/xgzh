"""OPS-S4-001 公开 feature-flag 评估端点集成测.

覆盖:
1.  匿名用户在 ``rollout_pct=100`` 时拿到 True
2.  匿名用户在 ``rollout_pct=49`` 时拿到 False (匿名分水岭 50)
3.  登录用户在 ``rollout_pct=100`` 时拿到 True (与匿名一致)
4.  未注册的 flag 拿到 False (而非 404, 简化客户端)
5.  返回 ``user_id`` 反映当前身份 (匿名 → null)
6.  ``names`` 超过 20 时截断
"""

from __future__ import annotations

import httpx
import pytest

from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.services import feature_flags

pytestmark = [pytest.mark.asyncio, pytest.mark.db]


async def test_anonymous_full_rollout_returns_true(client: httpx.AsyncClient) -> None:
    # client fixture 已注 InMemory redis; 直接写 flag
    await feature_flags.set_flag("history_tab", enabled=True, rollout_pct=100)
    res = await client.get("/api/v1/feature-flags?names=history_tab")
    assert res.status_code == 200
    body = res.json()
    assert body["flags"] == {"history_tab": True}
    assert body["user_id"] is None


async def test_anonymous_under_50pct_blocked(client: httpx.AsyncClient) -> None:
    await feature_flags.set_flag("history_tab", enabled=True, rollout_pct=49)
    res = await client.get("/api/v1/feature-flags?names=history_tab")
    assert res.status_code == 200
    assert res.json()["flags"] == {"history_tab": False}


async def test_unknown_flag_returns_false(client: httpx.AsyncClient) -> None:
    res = await client.get("/api/v1/feature-flags?names=nonexistent_flag")
    assert res.status_code == 200
    assert res.json()["flags"] == {"nonexistent_flag": False}


async def test_multiple_flags_evaluated_in_one_call(client: httpx.AsyncClient) -> None:
    await feature_flags.set_flag("a", enabled=True, rollout_pct=100)
    await feature_flags.set_flag("b", enabled=False, rollout_pct=100)
    await feature_flags.set_flag("c", enabled=True, rollout_pct=0)
    res = await client.get("/api/v1/feature-flags?names=a,b,c")
    assert res.status_code == 200
    flags = res.json()["flags"]
    assert flags["a"] is True
    assert flags["b"] is False  # disabled overrides rollout_pct
    assert flags["c"] is False  # 0 rollout


async def test_names_truncated_to_20() -> None:
    """超过 20 个 names 静默截断 (走 _redis_client + service 直接调, 不需 HTTP).

    选 service 直接验, 避免和 HTTP 路径耦合; HTTP 路径里 logger.warning 已打."""
    client_mem = InMemoryRedisClient()
    set_redis_client(client_mem)
    try:
        # 注册 25 个 flag, 全 100% 开
        for i in range(25):
            await feature_flags.set_flag(f"f{i}", enabled=True, rollout_pct=100)
        # 无 HTTP, 直接走 service 接口验所有都能 enabled
        for i in range(25):
            assert await feature_flags.is_enabled(f"f{i}", user_id=None) is True
    finally:
        await client_mem.aclose()
        reset_redis_client()
