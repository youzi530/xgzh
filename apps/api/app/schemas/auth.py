"""鉴权域 Pydantic schemas.

Sprint 1 内的鉴权流:
- BE-001: ``OTPSendRequest`` / ``OTPSendResponse``
- BE-002: ``PhoneLoginRequest`` / ``LoginResponse`` / ``UserPublic`` / ``TokenPair``
- BE-004: ``RefreshRequest`` / ``LogoutRequest`` / ``LogoutResponse``
- BE-005: ``WechatMpLoginRequest`` (后续 PR 加)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OTPSendRequest(BaseModel):
    phone: str = Field(
        ...,
        min_length=8,
        max_length=20,
        description="E.164 / 国内 11 位手机号. 后端会归一为 +86xxx",
        examples=["+8613800138000", "13800138000", "+85261234567"],
    )


class OTPSendResponse(BaseModel):
    sent: bool = True
    expires_in: int = Field(..., description="OTP 有效期 (秒)")
    request_id: str = Field(..., description="SMS 通道返回的请求 ID, 仅用于排查")
    masked_phone: str = Field(..., description="脱敏手机号, 前端可显示")


class PhoneLoginRequest(BaseModel):
    phone: str = Field(
        ...,
        min_length=8,
        max_length=20,
        description="与 OTP 发送时同一手机号 (任意 E.164 / 国内简写都行, 后端会归一)",
    )
    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        pattern=r"^\d{6}$",
        description="6 位数字 OTP",
        examples=["123456"],
    )


class TokenPair(BaseModel):
    """access + refresh 双 token. 字段名对齐 OAuth2 习惯, 前端不需要再翻译。"""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = Field(..., description="access_token 距离过期的秒数")
    refresh_expires_in: int = Field(..., description="refresh_token 距离过期的秒数")


class UserPublic(BaseModel):
    """对外暴露的用户字段子集. 不带 phone / wechat_openid 等敏感字段."""

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    nickname: str | None = None
    avatar_url: str | None = None
    region: str = "CN"
    invite_code: str
    status: int
    created_at: datetime


class LoginResponse(BaseModel):
    user: UserPublic
    tokens: TokenPair
    is_new_user: bool = Field(..., description="本次登录是否触发了自动注册")


class RefreshRequest(BaseModel):
    """``POST /auth/refresh`` 请求体. 仅这一次让 refresh_token 出现在 body 里;
    其它任何场景 refresh_token 都不应离开客户端。"""

    refresh_token: str = Field(..., min_length=10, description="登录或上次刷新返回的 refresh_token")


class LogoutRequest(BaseModel):
    """``POST /auth/logout`` 请求体. ``refresh_token`` 选填:
    - 若客户端能拿到 refresh, 强烈建议带上, 服务端会把它一并拉黑
    - 不带则只拉黑当前 access (从 Authorization header 拿). 这种情况下 refresh
      仍然可用, 直到自然过期, 是个 fallback, 不是默认行为。
    """

    refresh_token: str | None = Field(default=None, description="可选, 一并拉黑")


class LogoutResponse(BaseModel):
    logged_out: bool = True
    revoked_access: bool = Field(..., description="本次是否拉黑了 access token")
    revoked_refresh: bool = Field(..., description="本次是否拉黑了 refresh token")
