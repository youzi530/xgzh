"""缓存与限流装饰器.

提供 **生产可用** 的装饰器, 让业务层一行注解即可拿到:
- ``@cached(ttl_seconds, namespace)``     - JSON 序列化的函数级缓存
- ``@rate_limit(times, per_seconds, key_func)`` - 滑动窗口限流, 超限抛异常

设计要点
--------
1. **失败友好**: Redis I/O 失败不应让业务请求挂掉
   - ``@cached`` 读取/写入失败 → 走原函数, 仅 log warn
   - ``@rate_limit`` 不能容错: Redis 挂了等于关闸, 只能 raise (上层判断)
2. **可测试**: 不直接持有 client, 每次调用 ``get_redis_client()`` 取最新注入
3. **JSON-only**: ``@cached`` 强制要求被装饰函数返回值 JSON-serializable;
   反例 (Pydantic 模型对象) 由调用方 ``model_dump()`` 后再返回
4. **Key 命名遵循** ``.cursor/rules/40-database.mdc``::

       cache:<namespace>:<func_name>:<sha256(args+kwargs)[:16]>
       rate:<namespace>:<key_func() | func_name>

   全局再统一加 ``xgzh:`` 前缀（由 RedisClient 内部完成）。
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from loguru import logger

from app.cache.redis_client import get_redis_client

P = ParamSpec("P")
R = TypeVar("R")


class RateLimitExceeded(Exception):
    """超出 ``@rate_limit`` 配额. 上层 (FastAPI handler) 应转 429.

    Attributes:
        key: 触发限流的完整 Redis key (含 namespace, 不含 ``xgzh:`` 前缀)
        times: 配额上限
        per_seconds: 时间窗口
        retry_after: 建议等待秒数 (可空)
    """

    def __init__(
        self,
        key: str,
        times: int,
        per_seconds: int,
        retry_after: int | None = None,
    ) -> None:
        self.key = key
        self.times = times
        self.per_seconds = per_seconds
        self.retry_after = retry_after
        msg = f"Rate limit exceeded: {key} > {times}/{per_seconds}s"
        if retry_after:
            msg += f", retry after {retry_after}s"
        super().__init__(msg)


def _hash_args(args: tuple, kwargs: dict) -> str:
    """把 args/kwargs 序列化为 16 字节稳定哈希, 作为 cache key 后缀.

    用 ``default=str`` 兜底 datetime/Decimal 等; 非 JSON 友好对象不应直接
    作为缓存 key 参数 (会得到 stringify 后的形式), 调用方自觉。
    """
    payload = json.dumps([args, kwargs], default=str, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def cached(
    *,
    ttl_seconds: int,
    namespace: str = "default",
    skip_if_none: bool = True,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """缓存 async 函数返回值 (JSON 序列化).

    Args:
        ttl_seconds: TTL, 必须显式传 (按 rules/40-database.mdc 禁止永不过期)
        namespace: key 命名空间, 与其他业务隔离
        skip_if_none: 函数返回 None 时跳过写缓存 (避免错误穿透)

    被装饰函数返回值必须 JSON-serializable; 含 Decimal/datetime 等
    非原生类型由 ``json.dumps(..., default=str)`` 兜底, 反序列化时拿到字符串。

    Example::

        @cached(ttl_seconds=1800, namespace="ipo")
        async def fetch_ipo_basic(code: str) -> dict:
            return await akshare_client.fetch(code)
    """

    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds 必须 > 0 (禁止永不过期)")

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            client = get_redis_client()
            key = f"cache:{namespace}:{func.__name__}:{_hash_args(args, kwargs)}"

            try:
                payload = await client.get(key)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"cached GET failed (bypass cache): {e}")
                payload = None

            if payload is not None:
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning(f"cached payload corrupt, refetch: {key}")

            result = await func(*args, **kwargs)

            if skip_if_none and result is None:
                return result  # type: ignore[return-value]

            try:
                await client.set(
                    key, json.dumps(result, default=str), ttl_seconds=ttl_seconds
                )
            except (TypeError, ValueError) as e:
                logger.warning(f"cached SET serialize failed (non-fatal): {e}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"cached SET failed (non-fatal): {e}")

            return result

        return wrapper

    return decorator


def rate_limit(
    *,
    times: int,
    per_seconds: int,
    key_func: Callable[..., str] | None = None,
    namespace: str = "default",
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """限流装饰器 (固定窗口 INCR + EXPIRE).

    Args:
        times: 时间窗口内允许的最大调用次数
        per_seconds: 时间窗口长度（秒）
        key_func: 从被装饰函数的 ``*args/**kwargs`` 抽取限流 key (返回 str)。
                  传 None 时退化为按函数名做 **全局限流**。单用户限流务必
                  传 key_func 抽出 user_id, 否则一个用户能拖死全平台。
        namespace: 用于和其他限流域隔离, 默认 ``default``

    超限时 raise :class:`RateLimitExceeded`; 上层 handler 捕获后转 HTTP 429。

    Example::

        @rate_limit(
            times=1, per_seconds=60, namespace="otp",
            key_func=lambda phone: f"phone:{phone}",
        )
        async def send_otp(phone: str) -> None:
            ...
    """

    if times <= 0 or per_seconds <= 0:
        raise ValueError("times 与 per_seconds 必须 > 0")

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            client = get_redis_client()
            tag = key_func(*args, **kwargs) if key_func else func.__name__
            key = f"rate:{namespace}:{tag}"

            current = await client.incr_with_expire(key, per_seconds)
            if current > times:
                ttl = await client.ttl(key)
                retry_after = ttl if ttl > 0 else per_seconds
                raise RateLimitExceeded(
                    key=key,
                    times=times,
                    per_seconds=per_seconds,
                    retry_after=retry_after,
                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator
