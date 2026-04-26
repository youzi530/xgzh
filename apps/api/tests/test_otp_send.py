"""BE-001: POST /api/v1/auth/otp/send 端到端测试.

覆盖:
- 200: 快乐路径 (mock SMS, OTP 落 Redis, 返回脱敏手机)
- 400: 非法手机号 (空 / 国家码不支持 / 长度错)
- 429: 60s 内同手机号重复发送
- 502: SMS 通道侧失败 (注入抛异常的 adapter)
- 边界: 不同手机号互不影响; 输入 ``13800138000`` 与 ``+8613800138000`` 限流共享 key
- Redis 落库: ``xgzh:otp:{phone}`` 存在, 6 位数字, TTL <= 设定值

不依赖真 Redis / 真 SMS; 全部走 InMemoryRedisClient + MockSMSAdapter。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.adapters.sms import (
    MockSMSAdapter,
    SMSAdapter,
    SMSDeliveryError,
    SMSSendResult,
    reset_sms_adapter,
    set_sms_adapter,
)
from app.cache import (
    InMemoryRedisClient,
    namespaced_key,
    reset_redis_client,
    set_redis_client,
)
from app.main import create_app


@pytest.fixture
async def redis_client() -> AsyncIterator[InMemoryRedisClient]:
    client = InMemoryRedisClient()
    set_redis_client(client)
    yield client
    await client.aclose()
    reset_redis_client()


@pytest.fixture
async def mock_sms() -> AsyncIterator[MockSMSAdapter]:
    adapter = MockSMSAdapter()
    set_sms_adapter(adapter)
    yield adapter
    reset_sms_adapter()


@pytest.fixture
async def client(
    redis_client: InMemoryRedisClient,  # noqa: ARG001
    mock_sms: MockSMSAdapter,  # noqa: ARG001
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ------------------------- 200 happy path -------------------------


async def test_send_otp_happy_path_cn_short_form(
    client: httpx.AsyncClient,
    redis_client: InMemoryRedisClient,
    mock_sms: MockSMSAdapter,
) -> None:
    resp = await client.post("/api/v1/auth/otp/send", json={"phone": "13800138000"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sent"] is True
    assert body["expires_in"] == 300
    assert body["request_id"]
    assert body["masked_phone"].startswith("+86138") and "****" in body["masked_phone"]

    assert mock_sms.sent == [("+8613800138000", mock_sms.sent[0][1])]
    code = mock_sms.sent[0][1]
    assert code.isdigit() and len(code) == 6

    stored = await redis_client.get(namespaced_key("otp:+8613800138000"))
    assert stored == code

    ttl = await redis_client.ttl(namespaced_key("otp:+8613800138000"))
    assert 0 < ttl <= 300


async def test_send_otp_happy_path_e164_with_dashes(
    client: httpx.AsyncClient,
    redis_client: InMemoryRedisClient,
    mock_sms: MockSMSAdapter,
) -> None:
    resp = await client.post("/api/v1/auth/otp/send", json={"phone": "+852-6123-4567"})
    assert resp.status_code == 200, resp.text
    assert mock_sms.sent[0][0] == "+85261234567"
    assert await redis_client.get(namespaced_key("otp:+85261234567")) == mock_sms.sent[0][1]


async def test_send_otp_supports_sg(
    client: httpx.AsyncClient,
    mock_sms: MockSMSAdapter,
) -> None:
    resp = await client.post("/api/v1/auth/otp/send", json={"phone": "+6591234567"})
    assert resp.status_code == 200, resp.text
    assert mock_sms.sent[0][0] == "+6591234567"


# ------------------------- 400 invalid -------------------------


@pytest.mark.parametrize(
    "phone",
    [
        "",
        "abc",
        "12345",
        "+1234567890",  # +1 不在白名单
        "+86012345678",  # 不是 1 开头
        "+8613800",  # 太短
        "+8613800138000123",  # 太长
    ],
)
async def test_send_otp_invalid_phone_returns_400(
    client: httpx.AsyncClient, phone: str
) -> None:
    resp = await client.post("/api/v1/auth/otp/send", json={"phone": phone})
    assert resp.status_code in (400, 422), f"phone={phone} got {resp.status_code}: {resp.text}"
    if resp.status_code == 400:
        body = resp.json()
        assert body["detail"]["code"] == "invalid_phone"


async def test_send_otp_missing_phone_field(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/otp/send", json={})
    assert resp.status_code == 422


# ------------------------- 429 rate limit -------------------------


async def test_send_otp_rate_limited_within_60s(
    client: httpx.AsyncClient, mock_sms: MockSMSAdapter
) -> None:
    r1 = await client.post("/api/v1/auth/otp/send", json={"phone": "13800138000"})
    assert r1.status_code == 200, r1.text

    r2 = await client.post("/api/v1/auth/otp/send", json={"phone": "13800138000"})
    assert r2.status_code == 429
    body = r2.json()
    assert body["detail"]["code"] == "too_many_requests"
    assert body["detail"]["retry_after"] > 0
    assert "Retry-After" in r2.headers
    assert int(r2.headers["Retry-After"]) > 0

    assert len(mock_sms.sent) == 1


async def test_send_otp_rate_limit_uses_normalized_key(
    client: httpx.AsyncClient, mock_sms: MockSMSAdapter
) -> None:
    """``13800138000`` 与 ``+8613800138000`` 必须共享限流 key."""
    r1 = await client.post("/api/v1/auth/otp/send", json={"phone": "13800138000"})
    assert r1.status_code == 200

    r2 = await client.post("/api/v1/auth/otp/send", json={"phone": "+8613800138000"})
    assert r2.status_code == 429
    assert len(mock_sms.sent) == 1


async def test_send_otp_different_phones_isolated(
    client: httpx.AsyncClient, mock_sms: MockSMSAdapter
) -> None:
    r1 = await client.post("/api/v1/auth/otp/send", json={"phone": "13800138000"})
    r2 = await client.post("/api/v1/auth/otp/send", json={"phone": "13900139000"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert len(mock_sms.sent) == 2


# ------------------------- 502 SMS failure -------------------------


class _FailingAdapter(SMSAdapter):
    name = "failing"

    async def send_otp(self, phone: str, code: str, ttl_seconds: int) -> SMSSendResult:
        raise SMSDeliveryError(self.name, "channel offline", retryable=True)


async def test_send_otp_sms_delivery_error_returns_502(
    client: httpx.AsyncClient, redis_client: InMemoryRedisClient
) -> None:
    set_sms_adapter(_FailingAdapter())
    try:
        resp = await client.post("/api/v1/auth/otp/send", json={"phone": "13800138000"})
        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["code"] == "sms_delivery_failed"
        # SMS 失败时, 之前暂存的 OTP 必须被清除
        assert await redis_client.get(namespaced_key("otp:+8613800138000")) is None
    finally:
        reset_sms_adapter()


# ------------------------- otp_service 单测 -------------------------


async def test_otp_service_generate_code_is_6_digits() -> None:
    from app.services.otp_service import generate_otp_code

    seen: set[str] = set()
    for _ in range(50):
        code = generate_otp_code()
        assert code.isdigit()
        assert len(code) == 6
        seen.add(code)
    assert len(seen) > 1


async def test_otp_service_consume_clears_redis(
    redis_client: InMemoryRedisClient,
) -> None:
    from app.services.otp_service import consume_otp, fetch_stored_otp, store_otp

    await store_otp("+8613800138000", "123456", ttl_seconds=300)
    assert await fetch_stored_otp("+8613800138000") == "123456"

    await consume_otp("+8613800138000")
    assert await fetch_stored_otp("+8613800138000") is None
    _ = redis_client


# ------------------------- phone util 直测 -------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("13800138000", "+8613800138000"),
        ("+8613800138000", "+8613800138000"),
        ("8613800138000", "+8613800138000"),
        ("+86 138 0013 8000", "+8613800138000"),
        ("+852 6123 4567", "+85261234567"),
        ("+85361234567", "+85361234567"),
        ("+6591234567", "+6591234567"),
        ("+886912345678", "+886912345678"),
    ],
)
def test_normalize_phone_ok(raw: str, expected: str) -> None:
    from app.utils.phone import normalize_phone

    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "abc", "+1234567890", "+8613", "+8612345678901", "00112233445"],
)
def test_normalize_phone_rejects(raw: Any) -> None:
    from app.utils.phone import InvalidPhoneError, normalize_phone

    with pytest.raises(InvalidPhoneError):
        normalize_phone(raw)


def test_mask_phone_format() -> None:
    from app.utils.phone import mask_phone

    masked = mask_phone("+8613800138000")
    assert masked.startswith("+86138")
    assert masked.endswith("8000")
    assert "*" in masked
    assert mask_phone("garbage") == "***"
