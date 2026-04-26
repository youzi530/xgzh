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


# 滑动窗口 ZSET 原子脚本 (BE-S2-008 配额管理).
#
# 单脚本完成: ZREMRANGEBYSCORE 清旧 → ZADD 新成员 → ZCARD → EXPIRE
# 防止"读 ZCARD" 与 "ZADD" 之间被 trim 掉的成员重新算成 N+1, 与跨 RTT race.
# KEYS[1] = key, ARGV[1] = now_ms, ARGV[2] = window_ms, ARGV[3] = member,
# ARGV[4] = ttl_seconds. 返回写入后窗口内成员数.
_SLIDING_WINDOW_RECORD_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local member = ARGV[3]
local ttl = tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, ttl)
return redis.call('ZCARD', key)
"""

# 只清旧 + 数当前窗口数, 不写入 (BE-S2-008 check_quota 用).
_SLIDING_WINDOW_COUNT_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
return redis.call('ZCARD', key)
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
    async def delete_by_prefix(self, prefix: str) -> int: ...
    async def incr_with_expire(self, key: str, ttl_seconds: int) -> int: ...
    async def ttl(self, key: str) -> int: ...
    async def ping(self) -> bool: ...
    async def aclose(self) -> None: ...
    # ── 滑动窗口 (BE-S2-008 Agent 配额管理) ──────────────────────
    async def sliding_window_count(
        self,
        key: str,
        *,
        window_seconds: int,
        now_ms: int | None = None,
    ) -> int: ...
    async def sliding_window_record(
        self,
        key: str,
        *,
        window_seconds: int,
        member: str,
        now_ms: int | None = None,
        ttl_seconds: int | None = None,
    ) -> int: ...
    async def sliding_window_oldest_ms(
        self,
        key: str,
        *,
        window_seconds: int,
        now_ms: int | None = None,
    ) -> int | None: ...


class RealRedisClient:
    """生产 Redis 客户端（asyncio）."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._client = redis_async.from_url(url, decode_responses=True)

    async def get(self, key: str) -> str | None:
        return await self._client.get(namespaced_key(key))  # type: ignore[no-any-return]

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        await self._client.set(namespaced_key(key), value, ex=ttl_seconds)

    async def delete(self, key: str) -> int:
        return int(await self._client.delete(namespaced_key(key)))

    async def delete_by_prefix(self, prefix: str) -> int:
        """按前缀批量删除 keys (生产 Redis: SCAN + UNLINK).

        ``prefix`` 是 *逻辑前缀* (不含 ``xgzh:``), 内部会自动加.
        实现要点:
        - 用 ``SCAN`` 而不是 ``KEYS``: 后者会阻塞 Redis 单线程几十毫秒甚至秒级,
          ingest 任务删 100+ 缓存键时影响业务.
        - 用 ``UNLINK`` 而不是 ``DEL``: 前者把回收交给后台线程, 主线程响应更快.
          老版本 Redis (< 4.0) 没 UNLINK 就 fallback 到 DEL.
        - 分批 ``count=500`` 平衡单次 RTT 与 cursor 遍历轮数.
        - 异常向上传播: 缓存失效失败应让调用方 (例如 ``invalidate_namespace``)
          决定是否吞掉; 这里不静默防止业务以为已清.
        """
        full_prefix = namespaced_key(prefix)
        pattern = f"{full_prefix}*"
        deleted = 0
        async for raw_key in self._client.scan_iter(match=pattern, count=500):
            try:
                deleted += int(await self._client.unlink(raw_key))
            except (AttributeError, redis_async.ResponseError):
                deleted += int(await self._client.delete(raw_key))
        return deleted

    async def incr_with_expire(self, key: str, ttl_seconds: int) -> int:
        """原子: ``INCR`` + 仅首次执行 ``EXPIRE``. 防止双 INCR 间未设置 TTL.

        Lua 在 Redis 单线程中原子执行, 取代等价但有竞态的两步: ``INCR; EXPIRE``.
        """
        result = await self._client.eval(  # type: ignore[misc]
            _INCR_EXPIRE_LUA, 1, namespaced_key(key), str(ttl_seconds)
        )
        return int(result)

    async def ttl(self, key: str) -> int:
        return int(await self._client.ttl(namespaced_key(key)))

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())  # type: ignore[misc]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Redis ping failed: {e}")
            return False

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── 滑动窗口 (BE-S2-008) ────────────────────────────────────
    async def sliding_window_count(
        self,
        key: str,
        *,
        window_seconds: int,
        now_ms: int | None = None,
    ) -> int:
        """清旧 (score < now - window) + 返回当前窗口内成员数. 不写入."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        window_ms = window_seconds * 1000
        result = await self._client.eval(  # type: ignore[misc]
            _SLIDING_WINDOW_COUNT_LUA,
            1,
            namespaced_key(key),
            str(now),
            str(window_ms),
        )
        return int(result)

    async def sliding_window_record(
        self,
        key: str,
        *,
        window_seconds: int,
        member: str,
        now_ms: int | None = None,
        ttl_seconds: int | None = None,
    ) -> int:
        """ZADD 一条 ``(now_ms, member)`` + 清旧 + EXPIRE, 原子.

        - ``member`` 必须独立(uuid / msg_id), 避免重复 ZADD 同 score 同 member 导致计数不变
        - ``ttl_seconds`` 默认就是 window_seconds (再多没必要存)
        - 返回 ZCARD = 写入后窗口内成员数 (调用方拿这个判超额)
        """
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        window_ms = window_seconds * 1000
        ttl = ttl_seconds if ttl_seconds is not None else window_seconds
        result = await self._client.eval(  # type: ignore[misc]
            _SLIDING_WINDOW_RECORD_LUA,
            1,
            namespaced_key(key),
            str(now),
            str(window_ms),
            member,
            str(ttl),
        )
        return int(result)

    async def sliding_window_oldest_ms(
        self,
        key: str,
        *,
        window_seconds: int,
        now_ms: int | None = None,
    ) -> int | None:
        """清旧后返回最早一条的 score (ms 时间戳); 空 → None.

        retry_after 算法: ``oldest_ms + window_ms - now_ms`` 转秒.
        """
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        window_ms = window_seconds * 1000
        nk = namespaced_key(key)
        await self._client.zremrangebyscore(nk, "-inf", now - window_ms)
        rows = await self._client.zrange(nk, 0, 0, withscores=True)
        if not rows:
            return None
        return int(rows[0][1])


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
        # ZSET (用于滑动窗口): {key: list[(score_ms, member)]}, 按 score 升序维护.
        # 不参与 ``_store`` 的 KV 存储, 也独立于其 TTL — 滑动窗口的过期由
        # ZREMRANGEBYSCORE 语义负责, 不需要外层 EXPIRE 主动删 (空 zset 留在 dict
        # 也不会污染计数, 内存占用可忽略).
        self._zsets: dict[str, list[tuple[int, str]]] = {}
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

    async def delete_by_prefix(self, prefix: str) -> int:
        """按前缀批量删除 keys (内存版: dict 遍历).

        语义与 :meth:`RealRedisClient.delete_by_prefix` 一致, ``prefix`` 同样
        是逻辑前缀 (不带 ``xgzh:``), 内部加. 测试与单机降级场景用.
        """
        full_prefix = namespaced_key(prefix)
        async with self._lock:
            keys = [k for k in self._store if k.startswith(full_prefix)]
            for k in keys:
                del self._store[k]
            return len(keys)

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
            self._zsets.clear()

    # ── 滑动窗口 (BE-S2-008): InMemory 实现 ─────────────────────
    def _trim_zset(self, nk: str, cutoff_ms: int) -> None:
        """删 score <= cutoff_ms 的成员 (与 Redis ZREMRANGEBYSCORE inclusive 一致)."""
        z = self._zsets.get(nk)
        if not z:
            return
        # zset 按 score 升序; 找到第一个 score > cutoff 的 idx
        idx = 0
        while idx < len(z) and z[idx][0] <= cutoff_ms:
            idx += 1
        if idx > 0:
            self._zsets[nk] = z[idx:]

    async def sliding_window_count(
        self,
        key: str,
        *,
        window_seconds: int,
        now_ms: int | None = None,
    ) -> int:
        nk = namespaced_key(key)
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        async with self._lock:
            self._trim_zset(nk, now - window_seconds * 1000)
            return len(self._zsets.get(nk, []))

    async def sliding_window_record(
        self,
        key: str,
        *,
        window_seconds: int,
        member: str,
        now_ms: int | None = None,
        ttl_seconds: int | None = None,  # noqa: ARG002 - 内存版无需 TTL
    ) -> int:
        nk = namespaced_key(key)
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        async with self._lock:
            self._trim_zset(nk, now - window_seconds * 1000)
            z = self._zsets.setdefault(nk, [])
            # ZADD: same member 覆盖 score (Redis 行为); 模拟时找一下
            for i, (_, m) in enumerate(z):
                if m == member:
                    z.pop(i)
                    break
            # 按 score 升序插入 (单调时间戳大概率追加在尾)
            inserted = False
            for i, (s, _) in enumerate(z):
                if now < s:
                    z.insert(i, (now, member))
                    inserted = True
                    break
            if not inserted:
                z.append((now, member))
            return len(z)

    async def sliding_window_oldest_ms(
        self,
        key: str,
        *,
        window_seconds: int,
        now_ms: int | None = None,
    ) -> int | None:
        nk = namespaced_key(key)
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        async with self._lock:
            self._trim_zset(nk, now - window_seconds * 1000)
            z = self._zsets.get(nk)
            if not z:
                return None
            return z[0][0]


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
