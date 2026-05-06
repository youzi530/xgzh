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


class InviteRewardConfig(BaseModel):
    """BUG-S9-005 ``GET /invite/reward-config`` 响应.

    给 FE 渲染"邀请 N 人 → +M 天 VIP" 文案的源头配置. 走 settings 暴露,
    后续运营调整 ``invite_reward_n_users`` / ``invite_reward_vip_days``
    无需 FE 发版, 重启 BE 即可生效.

    匿名也可访问 (没用户敏感信息). 限流由 nginx / 全局 rate_limit 兜底.
    """

    threshold_n: int = Field(
        ...,
        ge=0,
        description="触发阈值: 累计成功邀请 ≥ N 个活跃用户 → 触发一次奖励. 0 = 关闭奖励",
    )
    vip_days: int = Field(
        ...,
        ge=0,
        description="奖励 VIP 天数. 0 = 关闭奖励",
    )
    enabled: bool = Field(
        ...,
        description="便利字段: threshold_n > 0 AND vip_days > 0 时为 true",
    )


__all__ = ["InviteBindRequest", "InviteBindResponse", "InviteRewardConfig"]
