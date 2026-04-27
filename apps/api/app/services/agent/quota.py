"""Agent 配额管理 (BE-S2-008).

定位
====
spec/09-sprint-2-backlog.md §配额: **免费 5 次/天 + VIP 无限 + 滑动窗口 + 友好提示**.

本模块给 ``app/api/v1/chat.py`` 入口提供 *进流前置闸门*:

1. ``check_quota(...)``     - 不写, 只读当前用量, 返回 :class:`QuotaStatus`
2. ``record_usage(...)``    - 真正进流后, 写一条 (扣一次额度)
3. ``QuotaExceeded``        - 端层捕获后转 HTTP 429 + 升级引导 payload

设计取舍
========
- **滑动窗口而非固定窗口**: 固定窗口在窗口边界会"突发翻倍"(00:59 跑 5 次, 01:00
  又能跑 5 次 = 1 分钟 10 次), spec 明确要"滑动"; 走 :meth:`RedisClientProtocol.
  sliding_window_record` 的 ZSET + Lua 原子实现, 单测和生产同语义.
- **check 与 record 分两步**: 进流前 check, 进流后 record. 让"用户连点 SSE 入口
  但我们不打算扣额"的情况能 retry — e.g. SSE 还没起来就 DB 异常, ``raise`` 后
  端层直接 HTTPException, 不计数. record 在 user_message 写 DB 之后第一时间扣.
- **VIP 走配置白名单兜底**: ``vip_memberships`` 表是 Sprint 3 的事, 当前 VIP
  的认定走 ``settings.vip_user_id_whitelist`` 一行 CSV; 接表后只换 ``_resolve_plan``
  函数, 接口签名 + 调用方 0 改动.
- **匿名走 IP key**: ``get_optional_user`` 返回 None 时端层把客户端 IP (拿
  ``X-Forwarded-For`` 第一段, fallback 到 ``request.client.host``) 作为 ``anon_key``
  传进来; 单 IP 较紧 (默认 2/天), 引导注册. 下面 ``_resolve_quota_key`` 拼成
  ``agent:anon:<ip>``; 单测可以传 ``"test-anon"`` 之类 stable string.
- **race 容忍**: check 与 record 不原子 (两次 RTT), 极端并发下可能有 1~2 次溢出;
  日均 5 次低频场景这点漂移可接受. Sprint 3 接审计后用单脚本 INCR & 立即拒, race
  完全消除 (也付出 retry_after 算法变粗的代价).

公开 API
========
- ``QuotaPlan`` (StrEnum)
- ``QuotaStatus`` (frozen dataclass)
- ``QuotaExceeded`` (Exception, 携带 :class:`QuotaStatus`)
- ``check_quota(*, user, anon_key) -> QuotaStatus``
- ``record_usage(*, user, anon_key, member) -> int``
- ``resolve_plan(user) -> QuotaPlan``  -- 给单测/端层日志看
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.cache import get_redis_client
from app.core.config import get_settings
from app.core.logging import logger
from app.db.models.user import User


class QuotaPlan(StrEnum):
    """计划类型. 当前 3 档, Sprint 3 接订阅会再加 ``trial`` / ``pro``.

    用 :class:`StrEnum` (Python 3.11+) 而非 ``(str, Enum)`` 双继承: 前者是后者的
    标准化封装, ``json.dumps`` / 日志拼接直接拿到字符串值不需要 ``.value``.
    """

    FREE = "free"
    VIP = "vip"
    ANONYMOUS = "anonymous"


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    """配额状态. ``limit / remaining = -1`` 表示 VIP 无限.

    端层把这个对象丢进 SSE error payload 让前端做 UI 决策 (升级 modal vs.
    `稍后再试` 提示). ``retry_after_seconds`` 仅在 ``has_quota=False`` 时有意义.
    """

    plan: QuotaPlan
    limit: int            # -1 = 无限
    used: int             # 当前窗口内已用次数 (record 之后立刻包含本次)
    remaining: int        # -1 = 无限; 否则 max(0, limit - used)
    window_seconds: int
    retry_after_seconds: int | None  # 超额时给前端"x 秒后再来"; 未超额 None

    @property
    def has_quota(self) -> bool:
        """VIP 永远 True; FREE/ANONYMOUS 时 ``remaining > 0``."""
        if self.limit < 0:
            return True
        return self.remaining > 0

    def to_dict(self) -> dict[str, object]:
        """SSE / HTTP body 友好的 dict (前端 ChatQuotaPayload 直接用)."""
        return {
            "plan": self.plan.value,
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "window_seconds": self.window_seconds,
            "retry_after_seconds": self.retry_after_seconds,
        }


class QuotaExceeded(Exception):  # noqa: N818 - 与 cache.RateLimitExceeded 命名风格保持一致
    """超配额异常. FastAPI 端层捕获后转 ``HTTPException(429, ...)``.

    ``status`` 字段携带 :class:`QuotaStatus`, 端层不需要重新查 Redis.

    命名说明: ruff N818 期望 ``*Error`` 后缀 (PEP 8 推荐), 但项目里已有
    :class:`app.cache.decorators.RateLimitExceeded` 走 ``*Exceeded`` 命名,
    用同一惯例维护语义聚类 (限流类异常都叫 Exceeded).
    """

    def __init__(self, status: QuotaStatus) -> None:
        self.status = status
        msg = (
            f"Agent quota exceeded: plan={status.plan.value} "
            f"used={status.used}/{status.limit if status.limit >= 0 else '∞'} "
            f"retry_after={status.retry_after_seconds}s"
        )
        super().__init__(msg)


# ─── 内部 ──────────────────────────────────────────────────────────────


def _resolve_plan(user: User | None) -> QuotaPlan:
    """决定用户当前 plan.

    - 没登录 → ``ANONYMOUS``
    - 登录 + UUID 在 ``settings.vip_user_id_whitelist`` → ``VIP``
    - 登录 + 手机号在 ``settings.vip_user_phone_whitelist`` → ``VIP``  (dev 友好)
    - 否则 → ``FREE``

    Sprint 3 接 ``vip_memberships`` 表后改这里读表 + 缓存; 接口签名不变.

    手机号匹配走归一化 bare 形式 (去 +86/+852/+65 前缀); 与 ``vip_user_phone_set``
    保持一致.
    """
    if user is None:
        return QuotaPlan.ANONYMOUS
    settings = get_settings()
    if settings.vip_user_id_set and str(user.user_id).lower() in settings.vip_user_id_set:
        return QuotaPlan.VIP
    phone_set = settings.vip_user_phone_set
    if phone_set and user.phone:
        # User.phone 落库时已归一化为 ``+86xxx`` E.164 (见 utils/phone.py),
        # 这里去掉 + 和国家区号后比较, 保持白名单灵活 (用户可在 .env 直接写 11 位裸号)
        bare = user.phone.lstrip("+")
        for prefix in ("86", "852", "65"):
            if bare.startswith(prefix) and len(bare) > len(prefix):
                bare = bare[len(prefix):]
                break
        if bare in phone_set:
            return QuotaPlan.VIP
    return QuotaPlan.FREE


def resolve_plan(user: User | None) -> QuotaPlan:
    """暴露 ``_resolve_plan`` 给端层 / 单测 (避免重复实现 VIP 判断)."""
    return _resolve_plan(user)


def _resolve_limit(plan: QuotaPlan) -> int:
    """从 settings 拿当前 plan 的 quota 上限. -1 = 无限."""
    settings = get_settings()
    if plan is QuotaPlan.VIP:
        return settings.agent_quota_vip_per_window
    if plan is QuotaPlan.ANONYMOUS:
        return settings.agent_quota_anonymous_per_window
    return settings.agent_quota_free_per_window


def _quota_key(plan: QuotaPlan, *, user: User | None, anon_key: str | None) -> str:
    """拼 Redis key (不含 ``xgzh:`` 前缀, 由 ``namespaced_key`` 自动加).

    设计:
    - ``rate:agent:user:<uuid>``   - 登录用户
    - ``rate:agent:anon:<ip>``     - 匿名 (端层把 IP 拼好传进来)
    - ``rate:agent:vip:<uuid>``    - VIP 单独 key (即便 -1 无限, 也走滑动窗口
                                     便于审计/Sprint 3 改有限额时 0 改 key)

    与现有 ``@rate_limit`` 装饰器的 ``rate:<namespace>:<tag>`` 命名约定对齐,
    ``namespace="agent"``; 这样 ops 在 Redis CLI ``SCAN xgzh:rate:agent:*`` 一查
    就能拿到所有 Agent 限流 key.
    """
    if plan is QuotaPlan.ANONYMOUS:
        # anon_key 可能为 None (端层未拿到 IP, 例如本地测试); 用 "unknown" 兜底,
        # 等价于"该 IP 不可识别 = 共享一个 key", 行为更紧而非更松, 安全侧.
        tag = anon_key or "unknown"
        return f"rate:agent:anon:{tag}"
    if user is None:
        # 不该走到这里: VIP/FREE 必须有 user; 防御性兜底
        return "rate:agent:anon:unknown"
    sub = "vip" if plan is QuotaPlan.VIP else "user"
    return f"rate:agent:{sub}:{user.user_id}"


def _retry_after_seconds(
    *, oldest_ms: int | None, window_seconds: int, now_ms: int
) -> int | None:
    """根据 ZSET 最早一条算 retry_after.

    最早那条 score + window 就是它"过期出窗"的绝对时间; 那个时间一到, 用户就
    能再调一次. 用 ``ceil`` 保证不会有"显示 5s 但实际还差 0.3s"的边界 retry.
    没有数据 (oldest_ms is None) 时返回 None.
    """
    if oldest_ms is None:
        return None
    expire_at_ms = oldest_ms + window_seconds * 1000
    delta_ms = expire_at_ms - now_ms
    if delta_ms <= 0:
        return None
    # ceil 到秒, 但不小于 1 (避免 UI 显示 "0 秒后重试" 引发死循环按)
    return max(1, (delta_ms + 999) // 1000)


# ─── 公开 API ───────────────────────────────────────────────────────────


async def check_quota(
    *,
    user: User | None,
    anon_key: str | None = None,
) -> QuotaStatus:
    """查当前用量, 不写. 返回 :class:`QuotaStatus`.

    流程:
    1. ``_resolve_plan`` 决定 plan
    2. 拿 limit (VIP=-1 无限直接返回 has_quota=True 不查 Redis, 节省一次 RTT)
    3. 走 ``RedisClientProtocol.sliding_window_count`` 拿当前用量 (顺便清旧)
    4. 算 ``remaining`` 与 ``retry_after_seconds`` 装入 status

    *不抛异常*. 调用方拿 status 自己决定要不要 raise (端层一般在 ``has_quota=False``
    时 raise :class:`QuotaExceeded`; 单测可以查不超额时的 used).
    """
    plan = _resolve_plan(user)
    limit = _resolve_limit(plan)
    settings = get_settings()
    window = settings.agent_quota_window_seconds

    if limit < 0:
        return QuotaStatus(
            plan=plan,
            limit=-1,
            used=0,
            remaining=-1,
            window_seconds=window,
            retry_after_seconds=None,
        )

    client = get_redis_client()
    key = _quota_key(plan, user=user, anon_key=anon_key)
    now_ms = int(time.time() * 1000)

    used = await client.sliding_window_count(
        key, window_seconds=window, now_ms=now_ms
    )
    remaining = max(0, limit - used)

    retry_after: int | None = None
    if remaining == 0:
        oldest = await client.sliding_window_oldest_ms(
            key, window_seconds=window, now_ms=now_ms
        )
        retry_after = _retry_after_seconds(
            oldest_ms=oldest, window_seconds=window, now_ms=now_ms
        )

    return QuotaStatus(
        plan=plan,
        limit=limit,
        used=used,
        remaining=remaining,
        window_seconds=window,
        retry_after_seconds=retry_after,
    )


async def record_usage(
    *,
    user: User | None,
    anon_key: str | None = None,
    member: str | None = None,
) -> QuotaStatus:
    """扣一次额度. 先 check 再 record (race 容忍, 见模块 docstring).

    - VIP 直接 noop, 返回 has_quota=True 的 status (审计层不写 Redis 也行,
      Sprint 3 真扣 50/天时改成走 record + 立即查 limit)
    - FREE / ANONYMOUS 走 ``sliding_window_record``: ZADD + 清旧 + EXPIRE 原子;
      返回写入后窗口内成员数, 这就是新的 ``used``
    - **超额时 raise** :class:`QuotaExceeded` (用 record 后的 used > limit 判,
      这样并发下也能兜住 1~2 次溢出 — 兜不住的极端情况是 Sprint 3 升级原子检查)

    ``member`` 默认是 uuid4, 让同一用户在同一毫秒下两次调用得到不同 ZSET 成员
    (Redis ZADD 同 member 同 score 会覆盖不计数, 漏算).
    """
    plan = _resolve_plan(user)
    limit = _resolve_limit(plan)
    settings = get_settings()
    window = settings.agent_quota_window_seconds

    if limit < 0:
        # VIP 无限: 不写 Redis, 直接告知 has_quota=True
        return QuotaStatus(
            plan=plan,
            limit=-1,
            used=0,
            remaining=-1,
            window_seconds=window,
            retry_after_seconds=None,
        )

    client = get_redis_client()
    key = _quota_key(plan, user=user, anon_key=anon_key)
    now_ms = int(time.time() * 1000)
    mem = member or str(uuid.uuid4())

    used = await client.sliding_window_record(
        key,
        window_seconds=window,
        member=mem,
        now_ms=now_ms,
        ttl_seconds=window,
    )

    remaining = max(0, limit - used)
    retry_after: int | None = None
    if used > limit:
        oldest = await client.sliding_window_oldest_ms(
            key, window_seconds=window, now_ms=now_ms
        )
        retry_after = _retry_after_seconds(
            oldest_ms=oldest, window_seconds=window, now_ms=now_ms
        )
        status = QuotaStatus(
            plan=plan,
            limit=limit,
            used=used,
            remaining=0,
            window_seconds=window,
            retry_after_seconds=retry_after,
        )
        logger.warning(
            f"agent.quota.exceeded plan={plan.value} key={key} used={used}/{limit}"
        )
        raise QuotaExceeded(status)

    return QuotaStatus(
        plan=plan,
        limit=limit,
        used=used,
        remaining=remaining,
        window_seconds=window,
        retry_after_seconds=None,
    )


__all__ = [
    "QuotaExceeded",
    "QuotaPlan",
    "QuotaStatus",
    "check_quota",
    "record_usage",
    "resolve_plan",
]
