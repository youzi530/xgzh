"""JWT 黑名单 (BE-004).

设计选择:
1. **基于 Redis 而非 DB**: 黑名单查询发生在每个鉴权请求, 必须 O(1); 同时所有条目天然
   过期 (TTL=token 剩余有效期), DB 既要 cron 清理又会随时间膨胀, 不划算。
2. **粒度: jti**: token 的 ``jti`` 是 256-bit 随机串, 全局唯一; 用 ``jti`` 拉黑
   而不是 ``user_id``, 是因为同一用户可在多设备并存登录, "踢这个设备"≠"踢全部"。
   将来 BE-011 的"踢全员"会再加一个 ``user_id`` 维度的 epoch 比对(用 token 的 iat
   <= user.token_invalidate_at 就拒收), 那是另一条线, 不在 BE-004 范围。
3. **Key 结构**: ``xgzh:blacklist:jti:{jti}`` value 固定 ``"1"``. 不存别的内容,
   每条占空间最小; ``namespaced_key`` 由 cache 层统一加前缀。
4. **TTL 取 ``max(exp - now, 1)`` 秒**: 比 token 剩余寿命多挂 1 秒以避免 redis
   过期与 ``decode_token`` 自校验之间的时序竞争, 也避免 ttl=0 写入立即被淘汰。
   token 自己已过期? ``decode_token`` 会先抛 ``TokenExpiredError``, 走不到黑名单
   分支, 所以这里不再特判 ``exp <= now`` (静默不写, 上层无副作用)。
5. **失败开放还是失败关闭**: Redis 故障时 ``is_jti_blacklisted`` 返回 ``False``
   (即放行) — 优先保证业务可用; 写入失败 (logout 拉黑) 则让上层路由感知到, 因为
   "用户以为自己登出了, 但其实没"是更严重的安全错觉。

后续:
- BE-004 之外, 任何"主动让 token 失效"场景 (改密码、强制踢下线、风控) 都通过这里。
- 对 "整体失效一个用户的所有 refresh" 这种粗粒度操作, 后续可以加
  ``user_token_epoch`` (Redis hash) + 颁发 token 时存进 token claim, 不在本 PR 内。
"""

from __future__ import annotations

import time

from loguru import logger

from app.cache import RedisClientProtocol, get_redis_client

BLACKLIST_KEY_NAMESPACE = "blacklist:jti"


def _blacklist_key(jti: str) -> str:
    return f"{BLACKLIST_KEY_NAMESPACE}:{jti}"


def _ttl_from_exp(expires_at: int, *, now: int | None = None) -> int:
    """剩余秒数. 已过期返回 0 (上层应短路, 不再写入)."""
    current = now if now is not None else int(time.time())
    return max(expires_at - current, 0)


async def blacklist_jti(
    jti: str,
    expires_at: int,
    *,
    redis: RedisClientProtocol | None = None,
    reason: str = "logout",
) -> bool:
    """把 ``jti`` 加入黑名单, 一直保留到原 token 自然过期。

    Args:
        jti: token 的 ``jti`` claim
        expires_at: token 的 ``exp`` claim (unix epoch 秒)
        reason: 仅打日志, 不写入 Redis (省空间)

    Returns:
        ``True`` 成功写入; ``False`` 不需要写入 (token 已过期). Redis 异常会原样抛出,
        让 logout 路由感知失败。
    """
    ttl = _ttl_from_exp(expires_at)
    if ttl <= 0:
        logger.debug(f"blacklist.skip.expired jti={jti} reason={reason}")
        return False

    client = redis or get_redis_client()
    await client.set(_blacklist_key(jti), "1", ttl_seconds=ttl)
    logger.info(f"blacklist.add jti={jti} ttl={ttl}s reason={reason}")
    return True


async def is_jti_blacklisted(
    jti: str,
    *,
    redis: RedisClientProtocol | None = None,
) -> bool:
    """命中即 ``True``. Redis 异常时 fail-open (返回 ``False`` 并打 warning),
    避免单点故障导致全员 401。"""
    try:
        client = redis or get_redis_client()
        return (await client.get(_blacklist_key(jti))) is not None
    except Exception as e:
        logger.warning(f"blacklist.check.fail jti={jti} err={e!r}; fail-open")
        return False


__all__ = [
    "BLACKLIST_KEY_NAMESPACE",
    "blacklist_jti",
    "is_jti_blacklisted",
]
