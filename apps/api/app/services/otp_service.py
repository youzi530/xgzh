"""OTP 生成 / 存储 / 发送服务.

流程 (BE-001 范围)::

    POST /auth/otp/send {phone}
        ↓ normalize_phone (utils/phone.py)
        ↓ rate_limit 60s/phone (cache/decorators.py, Lua 原子)
        ↓ generate_code (secrets, 6 位)
        ↓ Redis SET xgzh:otp:{phone} = code, TTL 5min
        ↓ SMS adapter send_otp (mock 走日志, aliyun 占位)
        ↓ 200 OK { sent: true, expires_in: 300 }

存储的是明文 OTP (Sprint 1)。BE-002 校验阶段会做常量时间比较 (hmac.compare_digest)。
后续若要再加固, 可在此 PR 之上换成 ``hashlib.scrypt(code + per_phone_salt)`` 存储,
BE-002 同步改 verify。当前 5min TTL 足以抵御暴力破解的现实威胁。
"""

from __future__ import annotations

import secrets

from loguru import logger

from app.adapters.sms import SMSDeliveryError, get_sms_adapter
from app.adapters.sms.base import SMSSendResult
from app.cache import get_redis_client
from app.utils.phone import mask_phone

OTP_REDIS_NAMESPACE = "otp"
OTP_CODE_LENGTH = 6


def _otp_key(phone: str) -> str:
    """Redis key (不含 ``xgzh:`` 前缀, 由 RedisClient 内部加上)."""
    return f"{OTP_REDIS_NAMESPACE}:{phone}"


def generate_otp_code() -> str:
    """6 位数字, 用 ``secrets`` 而非 ``random`` 避免可预测."""
    return f"{secrets.randbelow(10**OTP_CODE_LENGTH):0{OTP_CODE_LENGTH}d}"


async def store_otp(phone: str, code: str, ttl_seconds: int) -> None:
    """把 OTP 存入 Redis, key=``xgzh:otp:{phone}``, TTL=``ttl_seconds``."""
    client = get_redis_client()
    await client.set(_otp_key(phone), code, ttl_seconds=ttl_seconds)


async def fetch_stored_otp(phone: str) -> str | None:
    """读 OTP. BE-002 verify 用; 这里先放在 service 层避免 BE-002 重复实现。"""
    client = get_redis_client()
    return await client.get(_otp_key(phone))


async def consume_otp(phone: str) -> None:
    """OTP 一次性用完即删. BE-002 verify 成功后调."""
    client = get_redis_client()
    await client.delete(_otp_key(phone))


async def send_otp(phone: str, ttl_seconds: int) -> SMSSendResult:
    """生成 + 存储 + 发送. 仅做这三件事, 限流由路由装饰器负责.

    Returns:
        SMSSendResult: 不包含原始 OTP 码

    Raises:
        SMSDeliveryError: 通道发送失败 (上层应 502 或 retry)
    """
    code = generate_otp_code()
    await store_otp(phone, code, ttl_seconds=ttl_seconds)
    logger.info(f"otp.generated phone={mask_phone(phone)} ttl={ttl_seconds}s")

    try:
        adapter = get_sms_adapter()
        return await adapter.send_otp(phone, code, ttl_seconds=ttl_seconds)
    except SMSDeliveryError:
        # 发送失败时, 立即清掉刚存的 OTP, 避免用户已经收到旧 OTP 又拿到新的
        # 走老的成功流程
        await consume_otp(phone)
        raise
