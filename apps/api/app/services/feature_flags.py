"""OPS-S4-001 Feature flags 灰度旋钮.

设计要点:
1. **存储**: Redis ``hashmap`` (``xgzh:flags`` namespace), key=flag name, value=JSON
   ``{"enabled": bool, "rollout_pct": int, "updated_at": "<iso>"}``. 单点真源.
2. **判定**: ``is_enabled(flag, *, user_id)`` 走稳定 hash:
   ``int(blake2b(flag + ":" + user_id).hexdigest()[:8], 16) % 100 < rollout_pct``.
   - 用 ``flag + user_id`` 而不是只 ``user_id``: 避免"开 history_tab 5% 跟开
     industry_compare 5% 是同一拨人", 让不同 flag 的灰度群相互独立
   - blake2b 比 SHA-256 快 + 输出截断 8 hex (32 bit) 已够散列均匀
3. **匿名**: ``user_id=None`` 时按 ``rollout_pct >= 50`` 决定 (避免给匿名用户随机抽样,
   会让"刷新一次开关跳一次"这种诡异 UX)
4. **bootstrap**: 服务首次起来 Redis 没数据时, 读 ``settings.feature_flags_default``
   (JSON map) 回填一次, 让本地 / CI / 新部署的环境不需要手工 admin-write 才有 flags
5. **TTL / 缓存**: 服务端不在 process 里再做缓存 (Redis 本就在内网, 单次 GET 微秒级);
   前端走 ``feature_flags_cache_ttl_seconds`` 走自己的 localStorage TTL

公开 API:
    list_flags()                        -> 所有 flag 当前配置
    get_flag(name)                      -> 单 flag 配置 (None 则 ``rollout_pct=0``)
    set_flag(name, enabled, rollout_pct)-> admin-write
    delete_flag(name)
    is_enabled(name, *, user_id)        -> bool, 判定具体用户能否看到
    bootstrap_defaults()                -> lifespan 启动时调
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from app.cache import get_redis_client
from app.core.config import get_settings

FLAG_KEY_PREFIX = "flags:"  # 真实 redis key = ``xgzh:flags:<name>``
ROLLOUT_PCT_MIN = 0
ROLLOUT_PCT_MAX = 100
HASH_BUCKET_MOD = 100


@dataclass(frozen=True, slots=True)
class FlagConfig:
    name: str
    enabled: bool
    rollout_pct: int  # 0..100, 仅在 enabled=True 时生效
    updated_at: str  # ISO-8601, 给 admin UI / 审计

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, name: str, payload: dict[str, Any]) -> FlagConfig:
        enabled = bool(payload.get("enabled", False))
        rollout_pct = int(payload.get("rollout_pct", 0))
        rollout_pct = max(ROLLOUT_PCT_MIN, min(ROLLOUT_PCT_MAX, rollout_pct))
        updated_at = str(payload.get("updated_at") or datetime.now(UTC).isoformat())
        return cls(
            name=name,
            enabled=enabled,
            rollout_pct=rollout_pct,
            updated_at=updated_at,
        )


def _flag_key(name: str) -> str:
    """``flag_name`` → cache namespace key (不含 ``xgzh:`` 全局前缀)."""
    return f"{FLAG_KEY_PREFIX}{name}"


def _stable_bucket(flag_name: str, user_id: str) -> int:
    """0..99 的稳定桶号. 同 (flag, user) 组合永远落同一桶, 灰度命中可重放."""
    payload = f"{flag_name}:{user_id}".encode()
    digest = hashlib.blake2b(payload, digest_size=8).hexdigest()[:8]
    return int(digest, 16) % HASH_BUCKET_MOD


async def get_flag(name: str) -> FlagConfig | None:
    """返单 flag 当前配置. 没设过 → ``None`` (调用方按"未启用"处理)."""
    client = get_redis_client()
    raw = await client.get(_flag_key(name))
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"feature_flags.get.bad_json name={name}")
        return None
    return FlagConfig.from_payload(name, payload)


async def list_flags() -> list[FlagConfig]:
    """列所有 flag (admin 用). 注意: ``InMemoryRedisClient`` 用 dict 遍历; 真 Redis
    内部走 ``SCAN`` (同 ``invalidate_namespace`` 路径). 数量很少 (~10), 不分页."""
    client = get_redis_client()
    flags: list[FlagConfig] = []
    # 复用 ``delete_by_prefix`` 同款的 SCAN 能力: 这里要的是"枚举", 没现成 API
    # 因此走 store 内部, 但封装层是 protocol; 退化方案: list_flags 仅返
    # ``feature_flags_default`` 里登记的 + admin-write 过的 (调用方自带 names)
    # 简化: 列 names 由 caller 给, get_flags(names) 才是稳定 API
    # 这里采用 best-effort: 走 client 的 ``delete_by_prefix`` 同 prefix 不删
    # 改为枚举 (RealRedisClient 提供) → 落地到 InMemoryRedisClient 上时枚举 dict.
    # 这里直接用 redis client 的 namespaced 内部 key.
    # 为避免引入另一个 protocol method, list_flags 改为读 ``flag_names`` 注册表.
    raw = await client.get(_flag_key("__index__"))
    if raw is None:
        return flags
    try:
        names: list[str] = json.loads(raw)
    except json.JSONDecodeError:
        names = []
    for n in names:
        flag = await get_flag(n)
        if flag is not None:
            flags.append(flag)
    return flags


async def _register_flag_name(name: str) -> None:
    """把 ``name`` 加入 ``__index__`` 注册表 (幂等). 让 ``list_flags`` 不依赖 SCAN."""
    client = get_redis_client()
    raw = await client.get(_flag_key("__index__"))
    if raw is None:
        names: list[str] = []
    else:
        try:
            names = json.loads(raw)
        except json.JSONDecodeError:
            names = []
    if name in names:
        return
    names.append(name)
    await client.set(_flag_key("__index__"), json.dumps(names))


async def set_flag(
    name: str,
    *,
    enabled: bool,
    rollout_pct: int,
) -> FlagConfig:
    """admin-write: 写 / 改 flag. ``rollout_pct`` 自动钳制到 0..100."""
    rollout_pct = max(ROLLOUT_PCT_MIN, min(ROLLOUT_PCT_MAX, int(rollout_pct)))
    cfg = FlagConfig(
        name=name,
        enabled=enabled,
        rollout_pct=rollout_pct,
        updated_at=datetime.now(UTC).isoformat(),
    )
    client = get_redis_client()
    await client.set(_flag_key(name), json.dumps(cfg.to_dict()))
    await _register_flag_name(name)
    logger.info(
        f"feature_flags.set name={name} enabled={enabled} pct={rollout_pct}"
    )
    return cfg


async def delete_flag(name: str) -> bool:
    """admin-delete: 删 flag. 删完 ``is_enabled`` 等价于'未启用'. 返是否实际删了 1 个."""
    client = get_redis_client()
    n = await client.delete(_flag_key(name))
    if n > 0:
        # 同时从 __index__ 摘掉
        raw = await client.get(_flag_key("__index__"))
        if raw is not None:
            try:
                names = json.loads(raw)
            except json.JSONDecodeError:
                names = []
            if name in names:
                names = [x for x in names if x != name]
                await client.set(_flag_key("__index__"), json.dumps(names))
        logger.info(f"feature_flags.delete name={name}")
    return n > 0


async def is_enabled(name: str, *, user_id: str | None = None) -> bool:
    """**调用方主入口**: 判定指定用户当前能否看到 ``name`` 这个 flag.

    决策规则:
    - flag 不存在 / ``enabled=False`` → False
    - ``rollout_pct >= 100`` → True (全开)
    - ``rollout_pct <= 0`` → False (内部, 但启用)
    - ``user_id is None`` (匿名): 按 ``rollout_pct >= 50`` 决定; 不抽样, 防"刷新一次
      开关跳一次"的诡异 UX. 若产品要"5% 灰度也包含匿名", 单独开 flag (例如
      ``history_tab_anon``) 写 100/0 二选一即可
    - 其它: 走 ``stable_bucket(flag, user_id) < rollout_pct``
    """
    flag = await get_flag(name)
    if flag is None or not flag.enabled:
        return False
    if flag.rollout_pct >= ROLLOUT_PCT_MAX:
        return True
    if flag.rollout_pct <= ROLLOUT_PCT_MIN:
        return False
    if user_id is None:
        # 匿名: ≥50 视为"已基本全开, 包含未登录访客", 否则不放
        return flag.rollout_pct >= 50
    bucket = _stable_bucket(name, user_id)
    return bucket < flag.rollout_pct


async def bootstrap_defaults() -> int:
    """从 ``settings.feature_flags_default`` (JSON) 回填首次配置.

    幂等: 已写过的 flag 不覆盖 (admin 改过的不能被启动逻辑回滚). 仅在 Redis 缺失时写.
    返写入条数. lifespan 启动时调一次, 失败不抛 (告警阶段还没起好, 直接 log)."""
    settings = get_settings()
    raw = settings.feature_flags_default.strip()
    if not raw:
        return 0
    try:
        defaults: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"feature_flags.bootstrap.bad_json: {e}")
        return 0
    if not isinstance(defaults, dict):
        logger.warning("feature_flags.bootstrap.not_object")
        return 0

    written = 0
    for name, payload in defaults.items():
        if not isinstance(payload, dict):
            continue
        existing = await get_flag(name)
        if existing is not None:
            continue
        try:
            await set_flag(
                name,
                enabled=bool(payload.get("enabled", False)),
                rollout_pct=int(payload.get("rollout_pct", 0)),
            )
            written += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"feature_flags.bootstrap.set_fail name={name}: {e}")
    if written:
        logger.info(f"feature_flags.bootstrap written={written}")
    return written


__all__ = [
    "FlagConfig",
    "bootstrap_defaults",
    "delete_flag",
    "get_flag",
    "is_enabled",
    "list_flags",
    "set_flag",
]
