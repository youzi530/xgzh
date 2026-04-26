"""xgzh-api 缓存层（Redis 封装 + 装饰器）.

公开 API:
    cached(ttl_seconds, namespace)       - 函数级 JSON 缓存
    rate_limit(times, per_seconds, ...)  - 滑动窗口限流
    RateLimitExceeded                    - 超限异常 (FastAPI handler 应转 429)
    InMemoryRedisClient                  - 单测/单机轻量场景客户端
    RedisClientProtocol                  - 抽象接口
    get_redis_client / set_redis_client / reset_redis_client
                                         - client 注入与管理
    namespaced_key                       - 内部 key 前缀工具
"""

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

__all__ = [
    "InMemoryRedisClient",
    "REDIS_KEY_PREFIX",
    "RateLimitExceeded",
    "RedisClientProtocol",
    "cached",
    "get_redis_client",
    "namespaced_key",
    "rate_limit",
    "reset_redis_client",
    "set_redis_client",
]
