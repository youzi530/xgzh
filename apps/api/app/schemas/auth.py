"""鉴权域 Pydantic schemas.

Sprint 1 内的鉴权流:
- BE-001: ``OTPSendRequest`` / ``OTPSendResponse``
- BE-002: ``PhoneLoginRequest`` / ``LoginResponse`` / ``UserPublic`` / ``TokenPair``
- BE-004: ``RefreshRequest`` / ``LogoutRequest`` / ``LogoutResponse``
- BE-005: ``WechatMpLoginRequest``

Sprint 9 BUG-S9-001 扩展密码登录:
- ``PasswordRegisterRequest`` / ``PasswordLoginRequest`` / ``SetPasswordRequest``
- ``UserPublic`` 加 ``has_phone`` / ``has_email`` / ``has_password`` /
  ``has_wechat`` / ``profile_complete`` 5 个 derived 布尔字段, 让 FE
  能判断要不要跳"完善资料"页 (BUG-S9-002).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


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
    """对外暴露的用户字段子集. 不带 phone / email / wechat_openid 明文敏感字段.

    BUG-S9-001 / BUG-S9-002 起加 5 个 ``has_*`` / ``profile_complete`` 派生布尔,
    让 FE 决定 "要不要跳完善资料页". 不暴露 phone / email 明文是合规要求
    (PIPL §22 最小化原则), 仅返"是否已设置"标志位.
    """

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    nickname: str | None = None
    avatar_url: str | None = None
    region: str = "CN"
    invite_code: str
    status: int
    created_at: datetime
    # ─── BUG-S9 派生字段 ─────────────────────────────────────────────
    # 这 5 个不是 ORM 列, 在 ``model_validate(user)`` 时由 ``@model_validator``
    # 从 user 实例其它字段计算出. 走 ``@computed_field`` 的话 from_attributes
    # 不会触发, 只能 ``model_validator(mode='before')``.
    has_phone: bool = False
    has_email: bool = False
    has_password: bool = False
    has_wechat: bool = False
    profile_complete: bool = False

    @model_validator(mode="before")
    @classmethod
    def _derive_has_flags(cls, data: object) -> object:
        """从 ORM ``User`` 实例派生 ``has_*`` + ``profile_complete``.

        - 输入是 ``User`` ORM 实例时 (``from_attributes=True`` 路径): 用 getattr
        - 输入已经是 dict (例如单测 / 二次序列化): 走 dict.get; 让本 validator 幂等
        - profile_complete = (有 phone 或有 email) AND 有 password — 微信登录后
          没补充则为 False, 触发 FE 跳完善资料页. 老 OTP 用户 has_phone=True 但
          has_password=False, profile_complete=False 同样触发, 强制设密码 (q4=A)
        """
        if isinstance(data, dict):
            has_phone = bool(data.get("phone")) if "phone" in data else bool(
                data.get("has_phone")
            )
            has_email = bool(data.get("email")) if "email" in data else bool(
                data.get("has_email")
            )
            has_password = bool(data.get("password_hash")) if "password_hash" in data else bool(
                data.get("has_password")
            )
            has_wechat = bool(data.get("wechat_openid")) if "wechat_openid" in data else bool(
                data.get("has_wechat")
            )
            data["has_phone"] = has_phone
            data["has_email"] = has_email
            data["has_password"] = has_password
            data["has_wechat"] = has_wechat
            data["profile_complete"] = (has_phone or has_email) and has_password
            return data
        # ORM 实例路径 — 直接 getattr (mypy 看不出来 ORM 类型, 全用 getattr 兜底)
        has_phone = bool(getattr(data, "phone", None))
        has_email = bool(getattr(data, "email", None))
        has_password = bool(getattr(data, "password_hash", None))
        has_wechat = bool(getattr(data, "wechat_openid", None))
        # 用 dict 包装, 让 BaseModel 后续 from_attributes 能拿到这 5 个 derived
        result = {
            "user_id": getattr(data, "user_id", None),
            "nickname": getattr(data, "nickname", None),
            "avatar_url": getattr(data, "avatar_url", None),
            "region": getattr(data, "region", "CN"),
            "invite_code": getattr(data, "invite_code", None),
            "status": getattr(data, "status", None),
            "created_at": getattr(data, "created_at", None),
            "has_phone": has_phone,
            "has_email": has_email,
            "has_password": has_password,
            "has_wechat": has_wechat,
            "profile_complete": (has_phone or has_email) and has_password,
        }
        return result


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


class WechatMpLoginRequest(BaseModel):
    """微信小程序 ``wx.login`` 拿到的 ``code`` (5min 有效, 一次性)."""

    code: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="小程序端 wx.login() 回调返回的 code, 5 分钟有效, 一次性",
    )


# ─── BUG-S9-001 密码登录 schemas ─────────────────────────────────────

# 密码强度: 用户拍板 q3=A 宽松 6-32 字, 至少 1 数字.
# - max_length=32: 防止 bcrypt 72-byte 物理上限 (UTF-8 32 字最坏 128 字节;
#   security_password 还有底线兜底但 schema 应该提前拒)
# - 不强制大小写 / 符号: MVP 期 UX 优先, 上线后看 brute-force 数据再调
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 32
_PASSWORD_HAS_DIGIT = re.compile(r"\d")


def _validate_password_format(password: str) -> str:
    """密码字符级校验 — 长度 + 必含数字. 复用给 register / login / set 三处."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"密码至少 {PASSWORD_MIN_LENGTH} 字符")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"密码最长 {PASSWORD_MAX_LENGTH} 字符")
    if not _PASSWORD_HAS_DIGIT.search(password):
        raise ValueError("密码必须至少包含一个数字")
    return password


class PasswordRegisterRequest(BaseModel):
    """``POST /auth/register/password`` 请求体.

    - phone OR email 二选一 (任一即可); 都传也允许, 同事务里都写库
    - password 6-32 字, 至少 1 数字 (拍板 q3=A)
    - invite_code 选填: 注册时直接绑邀请人, 减少一次 RTT (与 ``POST /invite/bind`` 等价)
    """

    phone: str | None = Field(
        default=None,
        min_length=8,
        max_length=20,
        description="手机号 (E.164 / 国内 11 位); BE 会归一为 +86xxx",
    )
    email: EmailStr | None = Field(
        default=None,
        description="邮箱 (RFC 5321); BE 会归一为小写存储",
    )
    password: str = Field(
        ...,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description=f"密码 {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} 字, 至少含 1 数字",
    )
    invite_code: str | None = Field(
        default=None,
        min_length=4,
        max_length=16,
        description="邀请码 (选填); 注册时同事务绑邀请人, 与 POST /invite/bind 等价",
    )

    @model_validator(mode="after")
    def _at_least_one_credential(self) -> Self:
        if not self.phone and not self.email:
            raise ValueError("phone 与 email 至少填一个")
        _validate_password_format(self.password)
        return self


class PasswordLoginRequest(BaseModel):
    """``POST /auth/login/password`` 请求体.

    - identifier 自动判断 phone 还是 email (含 @ → email; 否则 phone)
    - 密码错与 user 不存在统一抛 ``invalid_credentials`` 防 enumeration attack
    """

    identifier: str = Field(
        ...,
        min_length=4,
        max_length=254,
        description="手机号或邮箱, 自动识别 (含 @ → email)",
    )
    password: str = Field(
        ...,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description=f"密码 {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} 字",
    )


class SetPasswordRequest(BaseModel):
    """``PUT /me/password`` 请求体 — 老用户首次设密码 / 改密码.

    - 首次设密 (current_password=None): 用户已经登录, 之前没 password_hash
      (老 OTP 用户或微信用户), 用 access token 鉴身, 直接 hash + 写库
    - 改密 (current_password 必填): 已经有 password, 需要先验旧密码再换新密码
    """

    password: str = Field(
        ...,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description=f"新密码 {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} 字, 至少含 1 数字",
    )
    current_password: str | None = Field(
        default=None,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
        description="旧密码 (改密时必填; 首次设密留空)",
    )

    @model_validator(mode="after")
    def _validate_format(self) -> Self:
        _validate_password_format(self.password)
        return self


__all__ = [
    "LoginResponse",
    "LogoutRequest",
    "LogoutResponse",
    "OTPSendRequest",
    "OTPSendResponse",
    "PASSWORD_MAX_LENGTH",
    "PASSWORD_MIN_LENGTH",
    "PasswordLoginRequest",
    "PasswordRegisterRequest",
    "PhoneLoginRequest",
    "RefreshRequest",
    "SetPasswordRequest",
    "TokenPair",
    "UserPublic",
    "WechatMpLoginRequest",
]
