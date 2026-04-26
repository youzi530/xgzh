"""Redis 客户端封装层.

为什么不直接用 ``redis.asyncio.Redis``?
1. 单测要可在无 Docker / 无网络 的机器上跑，需要一个等价语义的内存版 fake
2. 限流的 ``INCR + EXPIRE`` 要原子（防双 INCR 之间窗口被遗忘 EXPIRE）
   → 真 Redis 用 Lua 脚本 ``EVAL`` 一次往返；内存版用 ``asyncio.Lock``
3. 全 key 必须带统一前缀（``xgzh:``）防止与同一 Redis 内别的项目串扰
4. 全局唯一 client + 显式注入 hook，让测试可在不动业务代码的前提下替换实现

公开 API:
    namespaced_key(key)           - 内部用, 给 key 加前缀
    RedisClientProtocol           - 抽象接口（结构化类型, 不强制继承）
    RealRedisClient               - 包装 redis.asyncio
    InMemoryRedisClient           - 单测/单机 dev 用
    get_redis_client()            - 全局 singleton 工厂（lazy 初始化）
    set_redis_client(client)      - 测试 / DI 用：注入 client
    reset_redis_client()          - 测试用：清空 singleton, 让下次 get 重建
"""

from __future__ import annotations

import asyncio
import time
from typing import Protocol, runtime_checkable

import redis.asyncio as redis_async
from loguru import logger

from app.core.config import get_settings

REDIS_KEY_PREFIX = "xgzh:"

_INCR_EXPIRE_LUA = """
local current = redis.call('INCR', KEYS[1])
if tonumber(current) == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
return current
"""


def namespaced_key(key: str) -> str:
    """给 key 加 ``xgzh:`` 前缀（已带前缀则原样返回，便于复用 namespaced key）."""
    if key.startswith(REDIS_KEY_PREFIX):
        return key
    return f"{REDIS_KEY_PREFIX}{key}"


@runtime_checkable
class RedisClientProtocol(Protocol):
    """缓存层抽象接口. 真 Redis / InMemory 都遵循这套."""

    async def get(self, key: str) -> str | None: ...
    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None: ...
    async def delete(self, key: str) -> int: ...
    async def incr_with_expire(self, key: str, ttl_seconds: int) -> int: ...
    async def ttl(self, key: str) -> int: ...
    async def ping(self) -> bool: ...
    async def aclose(self) -> None: ...


class RealRedisClient:
    """生产 Redis 客户端（asyncio）."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._client = redis_async.from_url(url, decode_responses=True)

    async def get(self, key: str) -> str | None:
        return await self._client.get(namespaced_key(key))

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        await self._client.set(namespaced_key(key), value, ex=ttl_seconds)

    async def delete(self, key: str) -> int:
        return int(await self._client.delete(namespaced_key(key)))

    async def incr_with_expire(self, key: str, ttl_seconds: int) -> int:
        """原子: ``INCR`` + 仅首次执行 ``EXPIRE``. 防止双 INCR 间未设置 TTL.

        Lua 在 Redis 单线程中原子执行, 取代等价但有竞态的两步: ``INCR; EXPIRE``.
        """
        result = await self._client.eval(
            _INCR_EXPIRE_LUA, 1, namespaced_key(key), str(ttl_seconds)
        )
        return int(result)

    async def ttl(self, key: str) -> int:
        return int(await self._client.ttl(namespaced_key(key)))

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Redis ping failed: {e}")
            return False

    async def aclose(self) -> None:
        await self._client.aclose()


class InMemoryRedisClient:
    """内存版 Redis 客户端: 单测 / 单机 dev / Redis 故障降级用.

    存储格式::

        self._store[key] = (value, expire_at_monotonic | None)

    采用 **惰性过期**: 每次访问时检查 TTL, 不开后台清理协程, 单测里更可控。
    所有写操作走 ``asyncio.Lock`` 串行化, 让 ``incr_with_expire`` 在并发
    协程下也能给出和真 Redis Lua 等价的原子语义。
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float | None]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _is_expired(expire_at: float | None) -> bool:
        return expire_at is not None and time.monotonic() >= expire_at

    async def get(self, key: str) -> str | None:
        nk = namespaced_key(key)
        async with self._lock:
            entry = self._store.get(nk)
            if entry is None:
                return None
            value, expire_at = entry
            if self._is_expired(expire_at):
                del self._store[nk]
                return None
            return value

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        nk = namespaced_key(key)
        expire_at = time.monotonic() + ttl_seconds if ttl_seconds else None
        async with self._lock:
            self._store[nk] = (value, expire_at)

    async def delete(self, key: str) -> int:
        nk = namespaced_key(key)
        async with self._lock:
            return 1 if self._store.pop(nk, None) is not None else 0

    async def incr_with_expire(self, key: str, ttl_seconds: int) -> int:
        nk = namespaced_key(key)
        async with self._lock:
            entry = self._store.get(nk)
            if entry is None or self._is_expired(entry[1]):
                self._store[nk] = ("1", time.monotonic() + ttl_seconds)
                return 1
            current = int(entry[0]) + 1
            self._store[nk] = (str(current), entry[1])
            return current

    async def ttl(self, key: str) -> int:
        nk = namespaced_key(key)
        async with self._lock:
            entry = self._store.get(nk)
            if entry is None:
                return -2
            _, expire_at = entry
            if expire_at is None:
                return -1
            remaining = expire_at - time.monotonic()
            return max(int(remaining), 0)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        async with self._lock:
            self._store.clear()


_client_singleton: RedisClientProtocol | None = None


def get_redis_client() -> RedisClientProtocol:
    """返回全局 client. 第一次调用时按 ``settings.redis_url`` 初始化.

    特殊 schema ``memory://`` 强制使用 InMemory 实现, 适合本地无 Redis 环境
    或 CI 单测场景。
    """
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton

    settings = get_settings()
    url = settings.redis_url
    if url.startswith("memory://"):
        _client_singleton = InMemoryRedisClient()
        logger.info("Cache: using InMemoryRedisClient (memory:// schema)")
    else:
        _client_singleton = RealRedisClient(url)
        logger.info(f"Cache: using RealRedisClient ({url})")
    return _client_singleton


def set_redis_client(client: RedisClientProtocol) -> None:
    """显式注入 client. 主要给单测/集成测试使用."""
    global _client_singleton
    _client_singleton = client


def reset_redis_client() -> None:
    """清空 singleton, 下次 ``get_redis_client()`` 重新构造."""
    global _client_singleton
    _client_singleton = None
