"""BE-S5-004 反馈服务单元测 (无 PG, 仅限流 + 红线词扫描分支).

DB 落库走 ``tests/integration/test_feedback.py``.

覆盖:
1.  enforce_rate_limit 匿名 IP 配额 (3/5min)
2.  enforce_rate_limit 登录用户 配额 (10/1h)
3.  enforce_rate_limit 匿名 + 没 IP → fallback bucket
4.  enforce_rate_limit 登录用户独立桶, 与匿名 IP 不串桶
5.  retry_after 取自 ttl, 不为 0
"""

from __future__ import annotations

import uuid

import pytest

from app.cache import (
    InMemoryRedisClient,
    RateLimitExceeded,
    reset_redis_client,
    set_redis_client,
)
from app.services import feedback_service

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _redis():
    client = InMemoryRedisClient()
    set_redis_client(client)
    yield client
    await client.aclose()
    reset_redis_client()


async def test_anon_ip_3_per_5min() -> None:
    """匿名 IP 同 IP 第 4 次 → RateLimitExceeded."""
    ip = "1.2.3.4"
    for _ in range(3):
        await feedback_service.enforce_rate_limit(user_id=None, client_ip=ip)
    with pytest.raises(RateLimitExceeded) as excinfo:
        await feedback_service.enforce_rate_limit(user_id=None, client_ip=ip)
    err = excinfo.value
    assert err.times == 3
    assert err.per_seconds == 5 * 60
    assert err.retry_after is not None
    assert err.retry_after > 0
    assert "feedback:ip:1.2.3.4" in err.key


async def test_user_10_per_1h() -> None:
    """登录用户同 user_id 第 11 次 → RateLimitExceeded."""
    user_id = uuid.uuid4()
    for _ in range(10):
        await feedback_service.enforce_rate_limit(user_id=user_id, client_ip="1.1.1.1")
    with pytest.raises(RateLimitExceeded) as excinfo:
        await feedback_service.enforce_rate_limit(user_id=user_id, client_ip="1.1.1.1")
    err = excinfo.value
    assert err.times == 10
    assert err.per_seconds == 60 * 60
    assert f"feedback:user:{user_id}" in err.key


async def test_anon_no_ip_fallback_bucket() -> None:
    """匿名且 IP 缺失 → 走 ``_unknown`` 桶, 同样有限流, 防裸刷."""
    for _ in range(3):
        await feedback_service.enforce_rate_limit(user_id=None, client_ip=None)
    with pytest.raises(RateLimitExceeded) as excinfo:
        await feedback_service.enforce_rate_limit(user_id=None, client_ip=None)
    assert "feedback:ip:_unknown" in excinfo.value.key


async def test_user_and_anon_buckets_isolated() -> None:
    """登录用户的桶和匿名 IP 桶不串: 同一物理人切换登录态不应影响配额.

    场景: 用户匿名提了 3 条 → 登录后再提仍能继续 (各占自己桶).
    """
    ip = "5.5.5.5"
    user_id = uuid.uuid4()

    # 匿名先把 IP 桶打满
    for _ in range(3):
        await feedback_service.enforce_rate_limit(user_id=None, client_ip=ip)
    with pytest.raises(RateLimitExceeded):
        await feedback_service.enforce_rate_limit(user_id=None, client_ip=ip)

    # 登录后还能提 (走自己 user 桶)
    for _ in range(10):
        await feedback_service.enforce_rate_limit(user_id=user_id, client_ip=ip)
    with pytest.raises(RateLimitExceeded):
        await feedback_service.enforce_rate_limit(user_id=user_id, client_ip=ip)


async def test_different_ips_independent() -> None:
    """两个不同 IP 互不影响, 每个都享独立 3/5min 配额."""
    for _ in range(3):
        await feedback_service.enforce_rate_limit(user_id=None, client_ip="6.6.6.6")
    # 另一 IP 还能继续
    for _ in range(3):
        await feedback_service.enforce_rate_limit(user_id=None, client_ip="7.7.7.7")
