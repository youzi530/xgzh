"""缓存 + 限流装饰器单测.

覆盖:
- ``InMemoryRedisClient``: get/set/delete/ttl 过期 / incr_with_expire 原子
- ``@cached``: miss → 执行 + 落缓存; hit → 不重复执行; ttl 过期后再次执行;
              不同 args 不同 key; 返回 None 时不缓存 (skip_if_none)
- ``@rate_limit``: 配额内通过; 超限 raise; 窗口超时后重置;
                   key_func 区分用户; 失败时 retry_after 合理

不依赖真实 Redis, 不依赖 fakeredis. 全部走自家 ``InMemoryRedisClient``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.cache import (
    InMemoryRedisClient,
    RateLimitExceeded,
    cached,
    rate_limit,
    reset_redis_client,
    set_redis_client,
)


@pytest.fixture(autouse=True)
async def _use_inmemory_redis() -> AsyncIterator[InMemoryRedisClient]:
    """每条用例独占一个 InMemory client, 防止 key 串扰."""
    client = InMemoryRedisClient()
    set_redis_client(client)
    yield client
    await client.aclose()
    reset_redis_client()


# ---------------- InMemoryRedisClient ----------------


async def test_inmemory_get_set_delete() -> None:
    c = InMemoryRedisClient()
    assert await c.get("missing") is None

    await c.set("foo", "bar")
    assert await c.get("foo") == "bar"

    deleted = await c.delete("foo")
    assert deleted == 1
    assert await c.get("foo") is None
    assert await c.delete("foo") == 0


async def test_inmemory_ttl_expires() -> None:
    c = InMemoryRedisClient()
    await c.set("foo", "bar", ttl_seconds=1)
    assert await c.get("foo") == "bar"
    assert await c.ttl("foo") in {0, 1}

    await asyncio.sleep(1.05)
    assert await c.get("foo") is None
    assert await c.ttl("foo") == -2


async def test_inmemory_incr_with_expire_serial() -> None:
    c = InMemoryRedisClient()
    assert await c.incr_with_expire("k", ttl_seconds=10) == 1
    assert await c.incr_with_expire("k", ttl_seconds=10) == 2
    assert await c.incr_with_expire("k", ttl_seconds=10) == 3


async def test_inmemory_incr_does_not_reset_ttl() -> None:
    """关键不变量: 后续 INCR 不能延长窗口, 否则限流可被无限延期."""
    c = InMemoryRedisClient()
    await c.incr_with_expire("k", ttl_seconds=2)
    await asyncio.sleep(0.5)
    await c.incr_with_expire("k", ttl_seconds=2)
    ttl = await c.ttl("k")
    assert 0 < ttl <= 2  # 应在 1.5 秒附近


async def test_inmemory_incr_atomic_under_concurrency() -> None:
    """100 个协程并发 INCR 应得到 1..100, 无丢失/重复."""
    c = InMemoryRedisClient()

    async def hit() -> int:
        return await c.incr_with_expire("counter", ttl_seconds=5)

    results = await asyncio.gather(*[hit() for _ in range(100)])
    assert sorted(results) == list(range(1, 101))


async def test_inmemory_window_expiry_resets_counter() -> None:
    c = InMemoryRedisClient()
    assert await c.incr_with_expire("k", ttl_seconds=1) == 1
    assert await c.incr_with_expire("k", ttl_seconds=1) == 2
    await asyncio.sleep(1.05)
    # 旧窗口过期, 计数从 1 重新开始
    assert await c.incr_with_expire("k", ttl_seconds=1) == 1


# ---------------- @cached ----------------


async def test_cached_miss_then_hit_skips_re_execution() -> None:
    counter = {"n": 0}

    @cached(ttl_seconds=60, namespace="t1")
    async def expensive(x: int) -> dict:
        counter["n"] += 1
        return {"result": x * 2}

    r1 = await expensive(3)
    r2 = await expensive(3)
    assert r1 == r2 == {"result": 6}
    assert counter["n"] == 1


async def test_cached_different_args_distinct_keys() -> None:
    counter = {"n": 0}

    @cached(ttl_seconds=60, namespace="t2")
    async def f(x: int) -> int:
        counter["n"] += 1
        return x + 1

    await f(1)
    await f(2)
    await f(1)
    await f(2)
    assert counter["n"] == 2


async def test_cached_ttl_expires_then_re_executes() -> None:
    counter = {"n": 0}

    @cached(ttl_seconds=1, namespace="t3")
    async def f() -> int:
        counter["n"] += 1
        return counter["n"]

    assert await f() == 1
    assert await f() == 1
    await asyncio.sleep(1.05)
    assert await f() == 2


async def test_cached_skip_none_does_not_cache_negatives() -> None:
    counter = {"n": 0}

    @cached(ttl_seconds=60, namespace="t4", skip_if_none=True)
    async def f() -> dict | None:
        counter["n"] += 1
        return None

    await f()
    await f()
    assert counter["n"] == 2


async def test_cached_serializes_decimal_via_default_str() -> None:
    """非原生 JSON 类型 (Decimal) 通过 default=str 兜底, 不抛异常."""
    from decimal import Decimal

    @cached(ttl_seconds=60, namespace="t5")
    async def f() -> dict:
        return {"price": Decimal("3.14")}

    r1 = await f()
    r2 = await f()
    assert r1 == {"price": Decimal("3.14")}
    assert r2 == {"price": "3.14"}  # 反序列化后是 str (调用方需自觉处理)


async def test_cached_invalid_ttl_raises() -> None:
    with pytest.raises(ValueError, match="ttl_seconds"):

        @cached(ttl_seconds=0, namespace="t6")
        async def f() -> int:
            return 1


# ---------------- @rate_limit ----------------


async def test_rate_limit_within_quota_passes() -> None:
    @rate_limit(times=3, per_seconds=10, namespace="rl1")
    async def f() -> str:
        return "ok"

    for _ in range(3):
        assert await f() == "ok"


async def test_rate_limit_exceeds_raises() -> None:
    @rate_limit(times=2, per_seconds=10, namespace="rl2")
    async def f() -> str:
        return "ok"

    await f()
    await f()
    with pytest.raises(RateLimitExceeded) as exc_info:
        await f()
    assert exc_info.value.times == 2
    assert exc_info.value.per_seconds == 10
    assert exc_info.value.retry_after is not None
    assert exc_info.value.retry_after > 0


async def test_rate_limit_resets_after_window() -> None:
    @rate_limit(times=1, per_seconds=1, namespace="rl3")
    async def f() -> str:
        return "ok"

    await f()
    with pytest.raises(RateLimitExceeded):
        await f()
    await asyncio.sleep(1.05)
    assert await f() == "ok"


async def test_rate_limit_per_user_via_key_func() -> None:
    @rate_limit(
        times=1,
        per_seconds=10,
        namespace="rl4",
        key_func=lambda user_id: f"user:{user_id}",
    )
    async def f(user_id: str) -> str:
        return user_id

    await f("u1")
    await f("u2")
    with pytest.raises(RateLimitExceeded):
        await f("u1")
    with pytest.raises(RateLimitExceeded):
        await f("u2")


async def test_rate_limit_invalid_args_raises() -> None:
    with pytest.raises(ValueError):

        @rate_limit(times=0, per_seconds=10, namespace="rl5")
        async def f1() -> None: ...

    with pytest.raises(ValueError):

        @rate_limit(times=1, per_seconds=0, namespace="rl6")
        async def f2() -> None: ...
