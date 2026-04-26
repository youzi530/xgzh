"""SMS 通道适配层.

设计:
- ``SMSAdapter`` 协议统一 ``send_otp(phone, code, ttl_seconds)`` 接口
- ``MockSMSAdapter`` (dev): 直接打日志, 便于本地手测
- ``AliyunSMSAdapter`` (prod 占位): TODO Sprint 2 接入
- ``get_sms_adapter()`` 按 ``settings.sms_adapter`` 返回单例

为什么 protocol 而不是 abstract base?
- ``runtime_checkable`` 让单测可直接 ``isinstance(x, SMSAdapter)``
- 第三方包不需要继承也能塞进来 (鸭子类型)
"""

from app.adapters.sms.base import SMSAdapter, SMSDeliveryError, SMSSendResult
from app.adapters.sms.factory import get_sms_adapter, reset_sms_adapter, set_sms_adapter
from app.adapters.sms.mock import MockSMSAdapter

__all__ = [
    "MockSMSAdapter",
    "SMSAdapter",
    "SMSDeliveryError",
    "SMSSendResult",
    "get_sms_adapter",
    "reset_sms_adapter",
    "set_sms_adapter",
]
