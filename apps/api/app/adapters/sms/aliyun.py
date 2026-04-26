"""阿里云 SMS adapter — 占位实现.

接入计划: **Sprint 2** 用阿里云 dysmsapi 模板:
- 模板 ID 在阿里云控制台申请 (示例: SMS_xxxxxx, "您的验证码为 ${code}, 有效期 ${ttl} 分钟")
- 国际短信 (HK/SG/TW) 用 dysmsapi-intl 通道, 模板单独申请
- AccessKey 走 RAM 子账号, 仅授予 ``AliyunDysmsFullAccess``
- 监控: dysmsapi 投递回执 webhook 入 Redis Stream

当前阶段 (Sprint 1): 抛 NotImplementedError, 让 ``SMS_ADAPTER=aliyun`` 部署即知失败。
"""

from __future__ import annotations

from typing import Any

from app.adapters.sms.base import SMSAdapter, SMSDeliveryError, SMSSendResult


class AliyunSMSAdapter(SMSAdapter):
    name = "aliyun"

    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        sign_name: str,
        template_id: str,
        intl_template_id: str | None = None,
    ) -> None:
        if not (access_key_id and access_key_secret and sign_name and template_id):
            raise ValueError("aliyun SMS: access key / sign / template all required")
        self._ak = access_key_id
        self._sk = access_key_secret
        self._sign = sign_name
        self._template_id = template_id
        self._intl_template_id = intl_template_id

    async def send_otp(self, phone: str, code: str, ttl_seconds: int) -> SMSSendResult:
        # TODO(Sprint 2):
        #   1. import alibabacloud_dysmsapi20170525 client
        #   2. 国内模板 vs 国际模板按 country code 分流
        #   3. 重试: tenacity 3 次指数退避, 5xx 才重试
        #   4. 配额超限单独抛 SMSDeliveryError(retryable=False)
        raise SMSDeliveryError(
            provider=self.name,
            message="aliyun adapter not implemented yet (Sprint 2)",
            retryable=False,
        )

    @classmethod
    def from_settings(cls, settings: Any) -> AliyunSMSAdapter:
        return cls(
            access_key_id=getattr(settings, "aliyun_sms_access_key_id", ""),
            access_key_secret=getattr(settings, "aliyun_sms_access_key_secret", ""),
            sign_name=getattr(settings, "aliyun_sms_sign_name", ""),
            template_id=getattr(settings, "aliyun_sms_template_id", ""),
            intl_template_id=getattr(settings, "aliyun_sms_intl_template_id", "") or None,
        )
