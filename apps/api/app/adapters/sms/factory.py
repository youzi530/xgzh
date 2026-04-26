"""SMS adapter 工厂 + singleton 注入器.

调用方:
    from app.adapters.sms import get_sms_adapter
    adapter = get_sms_adapter()

测试 / DI 替换:
    set_sms_adapter(MockSMSAdapter())
    ...
    reset_sms_adapter()
"""

from __future__ import annotations

from loguru import logger

from app.adapters.sms.base import SMSAdapter
from app.adapters.sms.mock import MockSMSAdapter
from app.core.config import get_settings

_singleton: SMSAdapter | None = None


def get_sms_adapter() -> SMSAdapter:
    global _singleton
    if _singleton is not None:
        return _singleton

    settings = get_settings()
    name = (settings.sms_adapter or "mock").lower()
    if name == "mock":
        _singleton = MockSMSAdapter()
        logger.info("SMS: using MockSMSAdapter (dev only — code goes to log)")
    elif name == "aliyun":
        from app.adapters.sms.aliyun import AliyunSMSAdapter

        _singleton = AliyunSMSAdapter.from_settings(settings)
        logger.info(f"SMS: using AliyunSMSAdapter (sign={settings.aliyun_sms_sign_name!r})")
    else:
        raise ValueError(f"unknown SMS_ADAPTER={name!r}; expected mock | aliyun")
    return _singleton


def set_sms_adapter(adapter: SMSAdapter) -> None:
    global _singleton
    _singleton = adapter


def reset_sms_adapter() -> None:
    global _singleton
    _singleton = None
