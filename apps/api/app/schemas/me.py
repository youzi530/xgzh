"""BE-S5-003 当前用户域 schemas: 注销账号请求 + 响应.

``DELETE /api/v1/me`` 走这套 schema; 与 ``UserPublic`` (auth.py) 解耦,
注销不返回 PII.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


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


__all__ = [
    "DeleteMeRequest",
    "DeleteMeResponse",
]
