"""BE-S5-003 当前用户域 schemas: 注销账号请求 + 响应.

``DELETE /api/v1/me`` 走这套 schema; 与 ``UserPublic`` (auth.py) 解耦,
注销不返回 PII.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class DeleteMeRequest(BaseModel):
    """``DELETE /me`` 请求体 (optional). 用户可选填注销原因, 也可空 body.

    HTTP DELETE 通常无 body, 但 FastAPI 支持; 前端不传也可 (Pydantic 全 optional).
    """

    reason: str | None = Field(
        default=None,
        max_length=256,
        description="用户填的注销原因 (≤ 256 字, optional). 让运营了解流失原因.",
    )


class DeleteMeResponse(BaseModel):
    """``DELETE /me`` 成功响应; 200 OK.

    不返回 user / token / PII 任何字段, 让前端清楚"这账号已经走了"; 客户端必须本地
    清 token + 跳转登录页.
    """

    deleted: bool = Field(..., description="一律 true (失败走 4xx)")
    user_id: uuid.UUID = Field(
        ..., description="注销的用户 ID — 仅给客户端 / 审计 log 引用"
    )
    deleted_at: datetime = Field(..., description="软删时刻 (UTC)")
    real_purge_scheduled_at: datetime = Field(
        ...,
        description="预计真删时刻 (= deleted_at + grace_days, 默认 30d 后); "
        "PIPL §47 合规承诺",
    )
    audit_id: uuid.UUID = Field(
        ..., description="``user_deletions.deletion_id``, admin 审计追溯凭据"
    )


class UpdateMeRequest(BaseModel):
    """``PATCH /me`` 请求体 (BUG-S6.8-002 + BUG-S9-001 / 002).

    Sprint 6.8 仅支持 ``nickname``; Sprint 9 扩展 ``email`` 与 ``avatar_url``,
    让微信用户在"完善资料"页里一次 PATCH 请求把头像 + 邮箱补齐.

    全字段 optional, 用 ``model_dump(exclude_unset=True)`` 拿到非 None patch.
    """

    nickname: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        description="新昵称 (1-20 字; 不传则不改).",
    )
    # BUG-S9-001 邮箱 — 走 EmailStr 让 Pydantic 拦掉格式错; 后端落库前归一小写
    email: EmailStr | None = Field(
        default=None,
        description="新邮箱 (RFC 5321; 不传则不改). 后端落库前 normalize 成小写.",
    )
    # BUG-S9-002 头像 — mp 端 chooseAvatar 后, FE 把临时 path 上传到 OSS / BE
    # disk 后拿到 https URL, 再走 PATCH /me 写库
    avatar_url: str | None = Field(
        default=None,
        max_length=512,
        description="头像 https URL (≤ 512 字符; 不传则不改).",
    )


__all__ = [
    "DeleteMeRequest",
    "DeleteMeResponse",
    "UpdateMeRequest",
]
