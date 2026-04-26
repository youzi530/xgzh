"""SMS adapter 抽象."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SMSSendResult:
    """SMS 发送结果. 不包含原始 OTP 码（由 service 层保管）."""

    request_id: str
    provider: str
    success: bool = True


class SMSDeliveryError(Exception):
    """通道侧失败 (网络 / 配额 / 黑名单). 上层 retry 或转 502."""

    def __init__(self, provider: str, message: str, retryable: bool = False) -> None:
        self.provider = provider
        self.retryable = retryable
        super().__init__(f"[{provider}] {message}")


@runtime_checkable
class SMSAdapter(Protocol):
    """SMS 通道接口.

    实现要求:
    - 不在日志里打全 phone, 必须用 ``mask_phone()`` (utils/phone.py)
    - 不持久化 OTP 内容, 通道发完即弃
    - 失败抛 ``SMSDeliveryError``, 不要返回 ``success=False``, 让上层依赖一致
    """

    name: str

    async def send_otp(self, phone: str, code: str, ttl_seconds: int) -> SMSSendResult:
        """发送 OTP 短信.

        Args:
            phone: E.164 格式手机号 (已经 ``normalize_phone`` 过)
            code: 6 位数字字符串
            ttl_seconds: 服务端 OTP 有效期, 文案要带, 让用户知道窗口
        """
        ...
