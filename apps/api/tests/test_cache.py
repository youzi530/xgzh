"""缓存 + 限流装饰器单测.

覆盖:
- ``InMemoryRedisClient``: get/set/delete/ttl 过期 / incr_with_expire 原子 /
                           delete_by_prefix
- ``@cached``: miss → 执行 + 落缓存; hit → 不重复执行; ttl 过期后再次执行;
              不同 args 不同 key; 返回 None 时不缓存 (skip_if_none)
- ``@rate_limit``: 配额内通过; 超限 raise; 窗口超时后重置;
                   key_func 区分用户; 失败时 retry_after 合理
- ``invalidate_namespace``: 按 namespace 清 ``@cached`` 写入的所有 keys (Sprint 1.5);
                            其它 namespace / 其它前缀 key 不受影响; client 抛异常 fail-soft

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
    invalidate_namespace,
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


# ---------------- delete_by_prefix ----------------


async def test_inmemory_delete_by_prefix() -> None:
    """前缀边界精确: ``ipos:list`` 不应误删 ``ipos:list-ext`` 之类相邻 namespace."""
    c = InMemoryRedisClient()
    await c.set("cache:ipos:list:f1:abc", "v1")
    await c.set("cache:ipos:list:f2:def", "v2")
    await c.set("cache:ipos:list-ext:f1:xxx", "v3")  # 边界相邻
    await c.set("cache:ipos:detail:f1:y", "v4")  # 不同 namespace
    await c.set("rate:otp:phone:13800000000", "1")  # 不同前缀

    removed = await c.delete_by_prefix("cache:ipos:list:")
    assert removed == 2

    assert await c.get("cache:ipos:list:f1:abc") is None
    assert await c.get("cache:ipos:list:f2:def") is None
    assert await c.get("cache:ipos:list-ext:f1:xxx") == "v3"
    assert await c.get("cache:ipos:detail:f1:y") == "v4"
    assert await c.get("rate:otp:phone:13800000000") == "1"


async def test_inmemory_delete_by_prefix_zero_match() -> None:
    c = InMemoryRedisClient()
    await c.set("cache:foo:bar", "v")
    assert await c.delete_by_prefix("cache:nonexistent:") == 0
    assert await c.get("cache:foo:bar") == "v"


# ---------------- invalidate_namespace ----------------


async def test_invalidate_namespace_clears_only_target_namespace() -> None:
    """实测 ``@cached`` 写入后, ``invalidate_namespace`` 能精确清掉自己的 key."""
    miss_count = {"list": 0, "detail": 0}

    @cached(ttl_seconds=600, namespace="ipos:list")
    async def list_ipos(market: str) -> dict:
        miss_count["list"] += 1
        return {"market": market, "items": []}

    @cached(ttl_seconds=600, namespace="ipos:detail")
    async def get_detail(code: str) -> dict:
        miss_count["detail"] += 1
        return {"code": code}

    # 各填 2 条不同 args 的 cache
    await list_ipos("HK")
    await list_ipos("A")
    await get_detail("0700.HK")
    await get_detail("600519.SH")
    assert miss_count == {"list": 2, "detail": 2}

    # hit 不增长
    await list_ipos("HK")
    await get_detail("0700.HK")
    assert miss_count == {"list": 2, "detail": 2}

    # 只清 ipos:list, ipos:detail 不应被影响
    removed = await invalidate_namespace("ipos:list")
    assert removed == 2  # 两个不同 args 的 key

    # ipos:list 全部 miss → 重新执行
    await list_ipos("HK")
    await list_ipos("A")
    assert miss_count["list"] == 4

    # ipos:detail 仍 hit, 不重新执行
    await get_detail("0700.HK")
    await get_detail("600519.SH")
    assert miss_count["detail"] == 2


async def test_invalidate_namespace_multi_namespaces() -> None:
    """传多个 namespace 时, 每个独立调用 + 总数累加."""

    @cached(ttl_seconds=600, namespace="ipos:list")
    async def f1(x: int) -> int:
        return x

    @cached(ttl_seconds=600, namespace="ipos:detail")
    async def f2(x: int) -> int:
        return x

    await f1(1)
    await f1(2)
    await f2(1)

    removed = await invalidate_namespace("ipos:list", "ipos:detail")
    assert removed == 3


async def test_invalidate_namespace_empty_args_returns_zero() -> None:
    assert await invalidate_namespace() == 0


async def test_invalidate_namespace_fail_soft_on_client_error() -> None:
    """单个 namespace client 失败时, 函数 catch + warn, 不抛, 其它 ns 继续清.

    场景: Redis 网络抖动. 失效失败让 ingest 末尾照样成功落库, 最差 stale 10/30 min.
    """

    class BoomClient(InMemoryRedisClient):
        async def delete_by_prefix(self, prefix: str) -> int:
            if prefix == "cache:ipos:list:":
                raise RuntimeError("simulated redis outage")
            return await super().delete_by_prefix(prefix)

    boom = BoomClient()
    set_redis_client(boom)
    try:
        # 给 ipos:detail 塞 1 条
        await boom.set("cache:ipos:detail:f:abc", "v")

        # 不抛, 哪怕 ipos:list 抛异常
        removed = await invalidate_namespace("ipos:list", "ipos:detail")

        # ipos:list 计 0 (失败), ipos:detail 计 1 (成功)
        assert removed == 1
        assert await boom.get("cache:ipos:detail:f:abc") is None
    finally:
        await boom.aclose()


async def test_invalidate_namespace_does_not_touch_rate_limit_keys() -> None:
    """``invalidate_namespace`` 只清 ``cache:`` 前缀, ``rate:`` 限流 key 不受影响.

    防止 ingest 误把 OTP / agent 限流计数器一起清了, 等于绕过限流.
    """

    @cached(ttl_seconds=60, namespace="ipos:list")
    async def f(x: int) -> int:
        return x

    await f(1)

    # 模拟限流装饰器写过的 key
    @rate_limit(times=10, per_seconds=60, namespace="otp")
    async def send_otp() -> None:
        return None

    await send_otp()
    await send_otp()

    removed = await invalidate_namespace("ipos:list")
    assert removed == 1

    # 限流计数仍在 (再 hit 一次后应是 3)
    await send_otp()
    # 没法直接读 raw key (装饰器 hash key), 改为再调 8 次后第 11 次应被限流
    for _ in range(7):
        await send_otp()
    with pytest.raises(RateLimitExceeded):
        await send_otp()
