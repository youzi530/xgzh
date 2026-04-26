"""邀请码域 Pydantic schemas (BE-006)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InviteBindRequest(BaseModel):
    """``POST /invite/bind`` 请求体."""

    code: str = Field(
        ...,
        min_length=4,
        max_length=16,
        description="referrer 的邀请码 (即 referrer ``users.invite_code`` 字段值)",
    )

    @field_validator("code")
    @classmethod
    def _normalize(cls, v: str) -> str:
        # 大写归一; 用户在小程序里手输容易混 'a' / 'A', 数据库里我们存大写
        return v.strip().upper()


class InviteBindResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ok: bool = True
    referrer_user_id: uuid.UUID = Field(..., description="绑定到的 referrer 用户 ID")
    referrer_invite_code: str = Field(..., description="referrer 的邀请码 (大写)")
    bound_at_usage_count: int = Field(
        ...,
        description="绑定后该 invite_code 的累计使用次数 (本次 +1 之后的值)",
    )


__all__ = ["InviteBindRequest", "InviteBindResponse"]
