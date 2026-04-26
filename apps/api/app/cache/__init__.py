"""xgzh-api 缓存层（Redis 封装 + 装饰器 + 命名空间失效）.

公开 API:
    cached(ttl_seconds, namespace)       - 函数级 JSON 缓存
    rate_limit(times, per_seconds, ...)  - 滑动窗口限流
    RateLimitExceeded                    - 超限异常 (FastAPI handler 应转 429)
    invalidate_namespace(*namespaces)    - 按 ``@cached`` namespace 批量清缓存
    InMemoryRedisClient                  - 单测/单机轻量场景客户端
    RedisClientProtocol                  - 抽象接口
    get_redis_client / set_redis_client / reset_redis_client
                                         - client 注入与管理
    namespaced_key                       - 内部 key 前缀工具
"""

from __future__ import annotations

from loguru import logger

from app.cache.decorators import RateLimitExceeded, cached, rate_limit
from app.cache.redis_client import (
    REDIS_KEY_PREFIX,
    InMemoryRedisClient,
    RedisClientProtocol,
    get_redis_client,
    namespaced_key,
    reset_redis_client,
    set_redis_client,
)


async def invalidate_namespace(*namespaces: str) -> int:
    """按 ``@cached(namespace=...)`` 批量清空写入的所有 cache keys.

    ``@cached`` 写入的实际 key 形如 ``xgzh:cache:<namespace>:<func>:<hash>``。
    本函数把 ``cache:<namespace>:`` 这个逻辑前缀传给 client 的 ``delete_by_prefix``,
    生产 (RealRedisClient) 用 ``SCAN + UNLINK``, 测试 (InMemoryRedisClient) 用
    内存 dict 遍历, 语义一致.

    使用场景: ingest / write 路径完成后想立刻让缓存回源 (避免 stale)。
    例: ``run_ingest_a_job`` 末尾调
    ``await invalidate_namespace("ipos:list", "ipos:detail")``。

    设计要点:
    - 为什么"加冒号" (``cache:<ns>:``): 防止 ``"ipos:list"`` 误删 ``"ipos:list-2"``
      或 ``"ipos:listing"``。装饰器写入时也是 ``cache:<ns>:<func>:<hash>``,
      天然带冒号边界。
    - 为什么 fail-soft (catch + warn): 缓存失效失败不应让调度任务整体重试,
      最差就是 stale 多 10/30 min, 业务不致命. 但每一次失败都 log 让运维可追.
    - 为什么不批量串成一个 pattern: SCAN 不支持多 pattern (一次只能一条);
      多个 namespace 顺序删, 一个失败不影响后续。

    Args:
        *namespaces: cached 装饰器用过的 namespace 名 (不带 ``cache:`` 前缀,
                     如 ``"ipos:list"`` / ``"ipos:detail"``)

    Returns:
        累计实际删除的 key 数. 单个 namespace 失败时它的计数为 0,
        不抛异常 (其它 namespace 继续删).
    """
    if not namespaces:
        return 0

    client = get_redis_client()
    total = 0
    for ns in namespaces:
        prefix = f"cache:{ns}:"
        try:
            removed = await client.delete_by_prefix(prefix)
            total += removed
            if removed > 0:
                logger.info(
                    f"cache.invalidate_namespace ns={ns} removed={removed}"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"cache.invalidate_namespace ns={ns} failed (non-fatal): {e}"
            )
    return total


__all__ = [
    "InMemoryRedisClient",
    "REDIS_KEY_PREFIX",
    "RateLimitExceeded",
    "RedisClientProtocol",
    "cached",
    "get_redis_client",
    "invalidate_namespace",
    "namespaced_key",
    "rate_limit",
    "reset_redis_client",
    "set_redis_client",
]
