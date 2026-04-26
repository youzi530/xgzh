"""滑动窗口 ZSET 实现单测 (BE-S2-008).

覆盖 ``InMemoryRedisClient`` 上的三个高层 API:
- ``sliding_window_record`` (写入 + 清旧 + 返计数, 原子语义)
- ``sliding_window_count`` (只清旧 + 计数, 不写)
- ``sliding_window_oldest_ms`` (拿最早一条 score 算 retry_after)

关键不变量:
- 同 ``member`` 不重复计数 (Redis ZADD same-member 覆盖, 不增 ZCARD)
- 不同 ``member`` 走 ZADD 新条目, 计数 +1
- 出窗成员被 ZREMRANGEBYSCORE 清掉 (并由后续 record/count 触发清)
- ``oldest_ms`` 在所有成员都出窗后返回 None

不跑真 Redis: 内存版语义与 Redis 5+ ZSET + ZREMRANGEBYSCORE + ZCARD 等价,
RealRedisClient 走真 Lua 的部分另由 BE-S2-008 集成测 e2e 间接验证.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.cache import (
    InMemoryRedisClient,
    reset_redis_client,
    set_redis_client,
)


@pytest.fixture(autouse=True)
async def _use_inmemory_redis() -> AsyncIterator[InMemoryRedisClient]:
    client = InMemoryRedisClient()
    set_redis_client(client)
    yield client
    await client.aclose()
    reset_redis_client()


# ─── sliding_window_record ──────────────────────────────────────


async def test_record_first_member_returns_one() -> None:
    c = InMemoryRedisClient()
    n = await c.sliding_window_record(
        "k", window_seconds=60, member="m1", now_ms=1_000_000
    )
    assert n == 1


async def test_record_distinct_members_accumulate() -> None:
    c = InMemoryRedisClient()
    assert await c.sliding_window_record(
        "k", window_seconds=60, member="m1", now_ms=1_000
    ) == 1
    assert await c.sliding_window_record(
        "k", window_seconds=60, member="m2", now_ms=2_000
    ) == 2
    assert await c.sliding_window_record(
        "k", window_seconds=60, member="m3", now_ms=3_000
    ) == 3


async def test_record_same_member_does_not_double_count() -> None:
    """ZADD 同 member 同 score 不增长 ZCARD (Redis 行为)."""
    c = InMemoryRedisClient()
    assert await c.sliding_window_record(
        "k", window_seconds=60, member="m1", now_ms=1_000
    ) == 1
    # 重复同 member, 计数应该仍是 1
    assert await c.sliding_window_record(
        "k", window_seconds=60, member="m1", now_ms=2_000
    ) == 1


async def test_record_isolated_keys() -> None:
    """两个 key 计数互不干扰."""
    c = InMemoryRedisClient()
    await c.sliding_window_record("k1", window_seconds=60, member="m1", now_ms=1_000)
    await c.sliding_window_record("k1", window_seconds=60, member="m2", now_ms=2_000)
    n2 = await c.sliding_window_record(
        "k2", window_seconds=60, member="m1", now_ms=1_000
    )
    assert n2 == 1


async def test_record_evicts_out_of_window() -> None:
    """新写入触发 ZREMRANGEBYSCORE: 早于 (now - window) 的成员被清."""
    c = InMemoryRedisClient()
    await c.sliding_window_record(
        "k", window_seconds=10, member="old", now_ms=1_000_000
    )
    # 窗口 10s, 跳到 1_000_000 + 11_000 = 出窗 1s
    n = await c.sliding_window_record(
        "k", window_seconds=10, member="new", now_ms=1_011_000
    )
    # old 已出窗, new 是唯一在窗内的
    assert n == 1


# ─── sliding_window_count ──────────────────────────────────────


async def test_count_empty_returns_zero() -> None:
    c = InMemoryRedisClient()
    assert await c.sliding_window_count(
        "k", window_seconds=60, now_ms=1_000_000
    ) == 0


async def test_count_does_not_record() -> None:
    """count 是只读: 多次调用不应改变计数."""
    c = InMemoryRedisClient()
    await c.sliding_window_record("k", window_seconds=60, member="m1", now_ms=1_000)
    for _ in range(3):
        n = await c.sliding_window_count("k", window_seconds=60, now_ms=2_000)
        assert n == 1


async def test_count_evicts_out_of_window() -> None:
    """count 也要清旧 (不然 has_quota 永远 False)."""
    c = InMemoryRedisClient()
    await c.sliding_window_record("k", window_seconds=10, member="m1", now_ms=1_000)
    # 窗口外的 count
    assert await c.sliding_window_count(
        "k", window_seconds=10, now_ms=12_000
    ) == 0


# ─── sliding_window_oldest_ms ──────────────────────────────────


async def test_oldest_empty_returns_none() -> None:
    c = InMemoryRedisClient()
    assert await c.sliding_window_oldest_ms(
        "k", window_seconds=60, now_ms=1_000_000
    ) is None


async def test_oldest_returns_smallest_score() -> None:
    c = InMemoryRedisClient()
    await c.sliding_window_record("k", window_seconds=60, member="m1", now_ms=2_000)
    await c.sliding_window_record("k", window_seconds=60, member="m2", now_ms=1_000)
    await c.sliding_window_record("k", window_seconds=60, member="m3", now_ms=3_000)
    assert await c.sliding_window_oldest_ms(
        "k", window_seconds=60, now_ms=4_000
    ) == 1_000


async def test_oldest_after_evict() -> None:
    """出窗后重新计算最早一条; 全部出窗 → None."""
    c = InMemoryRedisClient()
    await c.sliding_window_record(
        "k", window_seconds=10, member="m1", now_ms=1_000
    )
    await c.sliding_window_record(
        "k", window_seconds=10, member="m2", now_ms=5_000
    )
    # now=12_000 时 m1 出窗, m2 (5_000) 还在; oldest = 5_000
    assert await c.sliding_window_oldest_ms(
        "k", window_seconds=10, now_ms=12_000
    ) == 5_000
    # now=20_000 时全部出窗
    assert await c.sliding_window_oldest_ms(
        "k", window_seconds=10, now_ms=20_000
    ) is None


# ─── 综合 ───────────────────────────────────────────────────────


async def test_full_cycle_record_then_count_then_oldest() -> None:
    """完整流: 写 5 条 → count=5 → oldest 是第一条 → 等过期 → count=0."""
    c = InMemoryRedisClient()
    for i in range(5):
        await c.sliding_window_record(
            "k", window_seconds=60, member=f"m{i}", now_ms=1_000 + i * 100
        )
    assert await c.sliding_window_count(
        "k", window_seconds=60, now_ms=2_000
    ) == 5
    assert await c.sliding_window_oldest_ms(
        "k", window_seconds=60, now_ms=2_000
    ) == 1_000
    # 全过期
    assert await c.sliding_window_count(
        "k", window_seconds=60, now_ms=70_000
    ) == 0
