"""推送 token 相关 Pydantic 模型 (BE-011).

设计原则:
- ``device_id`` 强制必填 + 非空: PG 中 ``UNIQUE (user_id, platform, device_id)`` 在
  ``device_id IS NULL`` 时不去重 (NULL 在 SQL 中互不相等), 这是个老坑.
  我们在 API schema 层强制非空, 保证 ON CONFLICT 行为可预期; 同时 ORM 层
  ``device_id`` 仍允许为 NULL, 给后续运营/导入历史数据留余地.
- 响应**绝不回显 ``token`` 内容**: APNs/FCM token 一旦泄露可被第三方推垃圾,
  风险大于收益; 客户端注册成功后自己已经持有这个 token, 不需要后端再给.
- ``platform`` 限白名单 ``ios / android / wxmp / h5``; 客户端传别的值
  Pydantic 直接 422, 不进表.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PushPlatform = Literal["ios", "android", "wxmp", "h5"]


class PushTokenRegisterRequest(BaseModel):
    platform: PushPlatform = Field(description="推送平台: ios / android / wxmp / h5")
    token: str = Field(
        min_length=8,
        max_length=4096,
        description="平台原生推送 token (APNs / FCM / wxmp openid / WebPush endpoint)",
    )
    device_id: str = Field(
        min_length=1,
        max_length=64,
        description="设备唯一标识. 同一 user+platform+device_id 复发 = 覆盖更新 token",
    )


class PushTokenRegisterResponse(BaseModel):
    """注册响应. ``token`` 不回显 (敏感)."""

    ok: bool = True
    id: int = Field(description="``push_tokens.id`` 主键")
    platform: PushPlatform
    device_id: str
    is_active: bool
    created: bool = Field(description="True = 新增; False = 同 device 已存在, 仅刷新 token")
    registered_at: datetime = Field(description="服务端落库时间 (新增=created_at, 覆盖=updated_at)")


class PushTokenUnregisterResponse(BaseModel):
    ok: bool = True
    platform: PushPlatform
    device_id: str
    removed: bool = Field(
        description="True = 真删了一行; False = 本来就没注册 (幂等返回 200)"
    )
