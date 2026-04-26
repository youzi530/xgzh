"""``services/agent/quota.py`` 单测 (BE-S2-008).

不依赖真 Redis (走 InMemoryRedisClient); 不依赖 DB (User 走轻量构造).

覆盖:
- ``resolve_plan``: 匿名 / FREE / VIP 三档分支 (vip_user_id_whitelist 单 / 多 / 空)
- ``check_quota``: VIP 不查 Redis 直接 has_quota=True;
                   FREE 在配额内 has_quota=True;
                   FREE 用满后 remaining=0 + retry_after 合理;
                   ANONYMOUS 默认 2/天上限对齐 settings
- ``record_usage``: VIP noop 不写 Redis;
                    FREE 第 1 / 2 / ... 次返回累计 used;
                    超额时 raise QuotaExceeded + status 携带 retry_after
- ``QuotaStatus.has_quota / to_dict``: -1 = 无限永远 True; remaining=0 = False
- 不同 user_id key 隔离: A 用满不影响 B
- 匿名 anon_key 隔离: 不同 IP 各自独立计数

关键 fixture:
- ``_use_inmemory_redis``: 每个用例一个干净 InMemory client
- ``override_settings``: monkeypatch ``get_settings()`` 返回的 quota 配置, 避免改
                         全局 .env / 防止单测之间漂移
- ``make_user``: 拿一个 user_id 可控的 User 对象 (不连 DB)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable

import pytest

from app.cache import InMemoryRedisClient, reset_redis_client, set_redis_client
from app.core.config import Settings, get_settings
from app.db.models.user import User
from app.services.agent.quota import (
    QuotaExceeded,
    QuotaPlan,
    QuotaStatus,
    check_quota,
    record_usage,
    resolve_plan,
)

# ─── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
async def _use_inmemory_redis() -> AsyncIterator[InMemoryRedisClient]:
    """每条用例独占内存 Redis, 防 key 串扰."""
    client = InMemoryRedisClient()
    set_redis_client(client)
    yield client
    await client.aclose()
    reset_redis_client()


@pytest.fixture
def make_user() -> Callable[..., User]:
    """构造一个可控 user_id 的 User; 不进 DB.

    User ORM 字段大多是 NOT NULL + server_default, 这里仅给 user_id (剩余字段
    quota 不读, 可以保持默认 None / 自动生成).
    """

    def _make(user_id: uuid.UUID | None = None) -> User:
        u = User()
        u.user_id = user_id or uuid.uuid4()
        return u

    return _make


@pytest.fixture
def override_settings(monkeypatch: pytest.MonkeyPatch) -> Callable[..., Settings]:
    """覆盖 settings (用 lru_cache get_settings 走的字段)."""
    base = get_settings()

    def _override(
        *,
        free_per_window: int | None = None,
        anon_per_window: int | None = None,
        vip_per_window: int | None = None,
        window_seconds: int | None = None,
        vip_user_id_whitelist: str | None = None,
    ) -> Settings:
        new = base.model_copy(
            update={
                k: v
                for k, v in {
                    "agent_quota_free_per_window": free_per_window,
                    "agent_quota_anonymous_per_window": anon_per_window,
                    "agent_quota_vip_per_window": vip_per_window,
                    "agent_quota_window_seconds": window_seconds,
                    "vip_user_id_whitelist": vip_user_id_whitelist,
                }.items()
                if v is not None
            }
        )
        # quota 模块每次都调 get_settings(), 替它返回新配置即可 (不改 lru cache)
        monkeypatch.setattr("app.services.agent.quota.get_settings", lambda: new)
        return new

    return _override


# ─── resolve_plan ──────────────────────────────────────────────────────


def test_resolve_plan_none_user_is_anonymous() -> None:
    assert resolve_plan(None) is QuotaPlan.ANONYMOUS


def test_resolve_plan_user_default_is_free(make_user: Callable[..., User]) -> None:
    assert resolve_plan(make_user()) is QuotaPlan.FREE


def test_resolve_plan_user_in_whitelist_is_vip(
    make_user: Callable[..., User],
    override_settings: Callable[..., Settings],
) -> None:
    uid = uuid.uuid4()
    override_settings(vip_user_id_whitelist=str(uid))
    assert resolve_plan(make_user(user_id=uid)) is QuotaPlan.VIP


def test_resolve_plan_csv_whitelist_with_spaces(
    make_user: Callable[..., User],
    override_settings: Callable[..., Settings],
) -> None:
    """CSV 多 ID + 空格 / 大小写都兼容."""
    a, b = uuid.uuid4(), uuid.uuid4()
    override_settings(
        vip_user_id_whitelist=f" {str(a).upper()} ,  {b!s} ,  ",
    )
    assert resolve_plan(make_user(user_id=a)) is QuotaPlan.VIP
    assert resolve_plan(make_user(user_id=b)) is QuotaPlan.VIP
    # 不在白名单的仍是 FREE
    assert resolve_plan(make_user()) is QuotaPlan.FREE


# ─── QuotaStatus / has_quota ───────────────────────────────────────────


def test_status_unlimited_has_quota_true() -> None:
    s = QuotaStatus(
        plan=QuotaPlan.VIP,
        limit=-1,
        used=0,
        remaining=-1,
        window_seconds=86400,
        retry_after_seconds=None,
    )
    assert s.has_quota is True
    d = s.to_dict()
    assert d["plan"] == "vip"
    assert d["limit"] == -1
    assert d["remaining"] == -1


def test_status_remaining_zero_has_quota_false() -> None:
    s = QuotaStatus(
        plan=QuotaPlan.FREE,
        limit=5,
        used=5,
        remaining=0,
        window_seconds=86400,
        retry_after_seconds=12345,
    )
    assert s.has_quota is False


# ─── check_quota ───────────────────────────────────────────────────────


async def test_check_vip_skips_redis(
    make_user: Callable[..., User],
    override_settings: Callable[..., Settings],
) -> None:
    uid = uuid.uuid4()
    override_settings(vip_user_id_whitelist=str(uid), vip_per_window=-1)
    s = await check_quota(user=make_user(user_id=uid))
    assert s.plan is QuotaPlan.VIP
    assert s.limit == -1
    assert s.has_quota is True


async def test_check_free_initially_full_quota(
    make_user: Callable[..., User],
    override_settings: Callable[..., Settings],
) -> None:
    override_settings(free_per_window=5, window_seconds=86400)
    s = await check_quota(user=make_user())
    assert s.plan is QuotaPlan.FREE
    assert s.limit == 5
    assert s.used == 0
    assert s.remaining == 5
    assert s.retry_after_seconds is None
    assert s.has_quota is True


async def test_check_anonymous_initially_full_quota(
    override_settings: Callable[..., Settings],
) -> None:
    override_settings(anon_per_window=2)
    s = await check_quota(user=None, anon_key="1.2.3.4")
    assert s.plan is QuotaPlan.ANONYMOUS
    assert s.limit == 2
    assert s.has_quota is True


async def test_check_does_not_consume(
    make_user: Callable[..., User],
    override_settings: Callable[..., Settings],
) -> None:
    """check_quota 多次调用不应改变 used (read-only)."""
    override_settings(free_per_window=5)
    user = make_user()
    for _ in range(3):
        s = await check_quota(user=user)
        assert s.used == 0


# ─── record_usage ──────────────────────────────────────────────────────


async def test_record_vip_is_noop(
    make_user: Callable[..., User],
    override_settings: Callable[..., Settings],
) -> None:
    uid = uuid.uuid4()
    override_settings(vip_user_id_whitelist=str(uid))
    s = await record_usage(user=make_user(user_id=uid))
    assert s.limit == -1
    assert s.used == 0  # VIP 不写 Redis 不计数


async def test_record_free_accumulates(
    make_user: Callable[..., User],
    override_settings: Callable[..., Settings],
) -> None:
    override_settings(free_per_window=5)
    user = make_user()
    for i in range(1, 6):
        s = await record_usage(user=user, member=f"m{i}")
        assert s.used == i
        assert s.remaining == 5 - i


async def test_record_exceeds_raises(
    make_user: Callable[..., User],
    override_settings: Callable[..., Settings],
) -> None:
    override_settings(free_per_window=2, window_seconds=86400)
    user = make_user()
    await record_usage(user=user, member="m1")
    await record_usage(user=user, member="m2")
    with pytest.raises(QuotaExceeded) as exc:
        await record_usage(user=user, member="m3")
    assert exc.value.status.used == 3
    assert exc.value.status.limit == 2
    assert exc.value.status.remaining == 0
    assert exc.value.status.retry_after_seconds is not None
    assert exc.value.status.retry_after_seconds > 0


async def test_record_isolation_per_user(
    make_user: Callable[..., User],
    override_settings: Callable[..., Settings],
) -> None:
    """A 用满不影响 B."""
    override_settings(free_per_window=1)
    a, b = make_user(), make_user()
    await record_usage(user=a)
    with pytest.raises(QuotaExceeded):
        await record_usage(user=a, member=str(uuid.uuid4()))
    # B 仍可调
    s = await record_usage(user=b)
    assert s.used == 1


async def test_record_anonymous_isolation_per_ip(
    override_settings: Callable[..., Settings],
) -> None:
    """不同 anon_key 各自独立计数."""
    override_settings(anon_per_window=1)
    s1 = await record_usage(user=None, anon_key="1.1.1.1")
    s2 = await record_usage(user=None, anon_key="2.2.2.2")
    assert s1.used == 1
    assert s2.used == 1
    # 同 IP 第二次超额
    with pytest.raises(QuotaExceeded):
        await record_usage(user=None, anon_key="1.1.1.1")


async def test_record_anonymous_unknown_ip_uses_fallback(
    override_settings: Callable[..., Settings],
) -> None:
    """anon_key=None / 空串走 'unknown' 共享 key (安全侧: 更紧不更松)."""
    override_settings(anon_per_window=1)
    await record_usage(user=None, anon_key=None)
    with pytest.raises(QuotaExceeded):
        await record_usage(user=None, anon_key=None)


async def test_record_default_member_unique(
    make_user: Callable[..., User],
    override_settings: Callable[..., Settings],
) -> None:
    """member 默认走 uuid4, 不会因为同 ms 调用导致计数停滞."""
    override_settings(free_per_window=10)
    user = make_user()
    used_seen = []
    for _ in range(3):
        s = await record_usage(user=user)  # 默认 uuid
        used_seen.append(s.used)
    assert used_seen == [1, 2, 3]


# ─── retry_after 逻辑 ──────────────────────────────────────────────────


async def test_retry_after_close_to_window(
    make_user: Callable[..., User],
    override_settings: Callable[..., Settings],
) -> None:
    """限了 1/120s 窗口, 用满后 retry_after 应在 (0, 120] 之内."""
    override_settings(free_per_window=1, window_seconds=120)
    user = make_user()
    await record_usage(user=user)
    with pytest.raises(QuotaExceeded) as exc:
        await record_usage(user=user)
    ra = exc.value.status.retry_after_seconds
    assert ra is not None
    assert 1 <= ra <= 120
