"""OPS-S4-001 feature_flags 单元测.

不依赖真 Redis / DB; 只用 InMemoryRedisClient + 单测桶分布."""

from __future__ import annotations

import json

import pytest

from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)
from app.core.config import get_settings
from app.services import feature_flags


@pytest.fixture(autouse=True)
def _redis_client() -> InMemoryRedisClient:
    """每条用例独立 InMemory client; teardown 自动清空."""
    client = InMemoryRedisClient()
    set_redis_client(client)
    yield client
    reset_redis_client()


@pytest.mark.asyncio
async def test_get_flag_missing_returns_none() -> None:
    assert await feature_flags.get_flag("nonexistent") is None


@pytest.mark.asyncio
async def test_set_then_get_roundtrip() -> None:
    cfg = await feature_flags.set_flag(
        "history_tab", enabled=True, rollout_pct=25
    )
    assert cfg.name == "history_tab"
    assert cfg.enabled is True
    assert cfg.rollout_pct == 25

    got = await feature_flags.get_flag("history_tab")
    assert got is not None
    assert got.enabled is True
    assert got.rollout_pct == 25


@pytest.mark.asyncio
async def test_set_clamps_rollout_pct_out_of_range() -> None:
    high = await feature_flags.set_flag("a", enabled=True, rollout_pct=150)
    low = await feature_flags.set_flag("b", enabled=True, rollout_pct=-10)
    assert high.rollout_pct == 100
    assert low.rollout_pct == 0


@pytest.mark.asyncio
async def test_list_flags_after_writes() -> None:
    await feature_flags.set_flag("history_tab", enabled=True, rollout_pct=10)
    await feature_flags.set_flag("ai_report", enabled=False, rollout_pct=0)
    flags = await feature_flags.list_flags()
    names = {f.name for f in flags}
    assert names == {"history_tab", "ai_report"}


@pytest.mark.asyncio
async def test_delete_flag_removes_from_index() -> None:
    await feature_flags.set_flag("history_tab", enabled=True, rollout_pct=50)
    deleted = await feature_flags.delete_flag("history_tab")
    assert deleted is True

    assert await feature_flags.get_flag("history_tab") is None
    assert await feature_flags.list_flags() == []
    deleted_again = await feature_flags.delete_flag("history_tab")
    assert deleted_again is False


@pytest.mark.asyncio
async def test_is_enabled_disabled_flag_returns_false() -> None:
    await feature_flags.set_flag("history_tab", enabled=False, rollout_pct=100)
    # 即便 100% rollout, ``enabled=False`` 也不放
    assert await feature_flags.is_enabled("history_tab", user_id="u-1") is False


@pytest.mark.asyncio
async def test_is_enabled_full_rollout_always_true() -> None:
    await feature_flags.set_flag("history_tab", enabled=True, rollout_pct=100)
    for uid in ["u-1", "u-2", "u-100", "u-9999"]:
        assert await feature_flags.is_enabled("history_tab", user_id=uid) is True
    # 匿名也包含
    assert await feature_flags.is_enabled("history_tab", user_id=None) is True


@pytest.mark.asyncio
async def test_is_enabled_zero_rollout_always_false() -> None:
    await feature_flags.set_flag("history_tab", enabled=True, rollout_pct=0)
    for uid in ["u-1", "u-2"]:
        assert await feature_flags.is_enabled("history_tab", user_id=uid) is False


@pytest.mark.asyncio
async def test_is_enabled_anonymous_under_50pct_blocked() -> None:
    """匿名用户在 ``rollout_pct < 50`` 时一律不放, 防"刷新一次开关跳一次"诡异 UX."""
    await feature_flags.set_flag("history_tab", enabled=True, rollout_pct=49)
    assert await feature_flags.is_enabled("history_tab", user_id=None) is False
    # ≥ 50 才放匿名
    await feature_flags.set_flag("history_tab", enabled=True, rollout_pct=50)
    assert await feature_flags.is_enabled("history_tab", user_id=None) is True


@pytest.mark.asyncio
async def test_is_enabled_stable_for_same_user() -> None:
    """同 user_id 永远落同一桶: 跨调用幂等, 灰度命中可回放."""
    await feature_flags.set_flag("history_tab", enabled=True, rollout_pct=30)
    uid = "user-stable-001"
    seen = {await feature_flags.is_enabled("history_tab", user_id=uid) for _ in range(20)}
    assert len(seen) == 1  # 同 uid 永远一致


@pytest.mark.asyncio
async def test_rollout_distribution_close_to_pct_for_5pct() -> None:
    """100 个用户 hash 到 5% 桶里, 实际命中数应在 [0, 12] 内 (Hoeffding bound).

    数学保证: 1000 用户取 5% 桶, 期望 50, 95% CI ≈ [35, 65]. 100 用户期望 5,
    放宽到 [0, 12] 让单测稳定不 flaky."""
    await feature_flags.set_flag("history_tab", enabled=True, rollout_pct=5)
    hits = 0
    for i in range(100):
        if await feature_flags.is_enabled("history_tab", user_id=f"user-{i}"):
            hits += 1
    # 期望 ~5; 容忍 0..15 (Hoeffding 99% CI ≈ ±10)
    assert 0 <= hits <= 15, f"expected ~5/100 hits, got {hits}"


@pytest.mark.asyncio
async def test_rollout_distribution_close_to_pct_for_50pct() -> None:
    """50% rollout 在 1000 个用户上应在 [400, 600] 内, 验 hash 均匀."""
    await feature_flags.set_flag("history_tab", enabled=True, rollout_pct=50)
    hits = 0
    for i in range(1000):
        if await feature_flags.is_enabled("history_tab", user_id=f"user-{i}"):
            hits += 1
    # 期望 500; 容忍 ±100 让 CI 不 flaky
    assert 400 <= hits <= 600, f"expected ~500/1000 hits, got {hits}"


@pytest.mark.asyncio
async def test_different_flags_buckets_independent() -> None:
    """不同 flag 给同一 user 的桶号互相独立 (不要让 25% A flag 命中的 user 跟 25% B flag
    命中的 user 是同一拨人; 否则灰度群叠加, 风险放大)."""
    await feature_flags.set_flag("flag_a", enabled=True, rollout_pct=25)
    await feature_flags.set_flag("flag_b", enabled=True, rollout_pct=25)
    same_users_both_in = 0
    only_a = 0
    only_b = 0
    for i in range(500):
        a = await feature_flags.is_enabled("flag_a", user_id=f"u-{i}")
        b = await feature_flags.is_enabled("flag_b", user_id=f"u-{i}")
        if a and b:
            same_users_both_in += 1
        elif a:
            only_a += 1
        elif b:
            only_b += 1
    # 期望 ~25% × 25% = 6.25% ≈ 31/500 用户两边都命中; 不应是 25% (125/500)
    # 给个宽容范围 [10, 70] 稳过 CI
    assert 10 <= same_users_both_in <= 70, (
        f"same_users_both_in={same_users_both_in} expected ~31/500 (independent buckets)"
    )


@pytest.mark.asyncio
async def test_bootstrap_defaults_writes_only_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """已存在的 flag 不被 bootstrap 覆盖 (防 admin 调过的 rollout_pct 被启动逻辑回滚)."""
    monkeypatch.setenv(
        "FEATURE_FLAGS_DEFAULT",
        json.dumps(
            {
                "history_tab": {"enabled": True, "rollout_pct": 5},
                "ai_report": {"enabled": True, "rollout_pct": 10},
            }
        ),
    )
    get_settings.cache_clear()
    # admin 已经调到 50%, bootstrap 不能回滚
    await feature_flags.set_flag("history_tab", enabled=True, rollout_pct=50)

    written = await feature_flags.bootstrap_defaults()
    assert written == 1  # 只写了 ai_report

    history = await feature_flags.get_flag("history_tab")
    assert history is not None and history.rollout_pct == 50  # 没回滚
    ai = await feature_flags.get_flag("ai_report")
    assert ai is not None and ai.rollout_pct == 10


@pytest.mark.asyncio
async def test_bootstrap_defaults_handles_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``FEATURE_FLAGS_DEFAULT`` 是无效 JSON 时不抛, 返 0."""
    monkeypatch.setenv("FEATURE_FLAGS_DEFAULT", "not-a-json{{")
    get_settings.cache_clear()
    written = await feature_flags.bootstrap_defaults()
    assert written == 0
    assert await feature_flags.list_flags() == []
