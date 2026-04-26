"""Mock SMS adapter — 开发 / CI 专用.

直接把 OTP 打到日志, 便于本地手测; 永远成功, 永远不 retry。
**绝对禁止在生产环境使用** (会泄露 OTP 到 stdout 日志收集系统)。
"""

from __future__ import annotations

import uuid

from loguru import logger

from app.adapters.sms.base import SMSAdapter, SMSSendResult
from app.utils.phone import mask_phone


class MockSMSAdapter(SMSAdapter):
    name = "mock"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []  # 测试用: 记录所有发送过的 (phone, code)

    async def send_otp(self, phone: str, code: str, ttl_seconds: int) -> SMSSendResult:
        request_id = uuid.uuid4().hex[:16]
        masked = mask_phone(phone)
        # ⚠️ 注意: 这里是 dev/CI 用, 真实 phone+code 会进日志, 仅本地可见
        logger.info(f"[MOCK SMS] to={phone} code={code} ttl={ttl_seconds}s rid={request_id}")
        logger.info(f"[MOCK SMS] (masked) to={masked} - delivered, please check log above")
        self.sent.append((phone, code))
        return SMSSendResult(request_id=request_id, provider=self.name, success=True)
