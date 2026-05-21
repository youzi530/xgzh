"""Admin 用户管理 schemas (Sprint 10 BE-S10-004 / BE-S10-005).

与 ``schemas/auth.py UserPublic`` 的区别:
- ``UserPublic`` 给 **当前用户自己** 看 (我的页), 隐藏 phone / email 明文 (PIPL §22 最小化).
- ``AdminUserListItem`` / ``AdminUserDetail`` 给 **admin 看别的用户**, 出 phone / email
  脱敏后明文 (审计排查需要). 调用方必须走 ``get_current_admin`` 鉴权依赖, 否则不应触达.

mask 策略:
- phone: ``mask_phone`` 取头 3 尾 4 (`138****8000`)
- email: ``mask_email`` 取首字母+本地 4 字 + 完整 domain
- nickname / avatar_url: 不脱敏 (本来就给用户社交身份用)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AdminUserListItem(BaseModel):
    """Admin 用户列表项 (5 endpoint 中的列表/搜索 GET /admin/users 返回).

    精简版 (无 VIP detail / no invite count) — 列表性能优先, 详情走单独 GET.
    """

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    phone_masked: str | None = Field(
        default=None,
        description="脱敏手机 (`138****8000`); 无 phone 时 None",
    )
    email_masked: str | None = Field(
        default=None,
        description="脱敏邮箱 (`a****@example.com`); 无 email 时 None",
    )
    nickname: str | None = None
    avatar_url: str | None = None
    region: str
    is_admin: bool
    status: int = Field(..., description="1=active, 0=disabled, -1=banned")
    is_deleted: bool = Field(..., description="是否已软删 (deleted_at NOT NULL)")
    vip_status: str | None = Field(
        default=None,
        description="VIP 状态; 无 membership 时 None (trialing/active/expired/cancelled)",
    )
    vip_end_at: datetime | None = Field(
        default=None,
        description="VIP 到期时间; 无 membership 时 None",
    )
    created_at: datetime


class AdminUserListResponse(BaseModel):
    """``GET /admin/users`` 响应; 分页 + 总数."""

    items: list[AdminUserListItem]
    total: int = Field(..., description="符合筛选条件的总记录数 (用于前端计算总页数)")
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


class AdminUserDetail(BaseModel):
    """单用户详情 (`GET /admin/users/{id}` + 写操作返值).

    比 ListItem 多: invite_count + invited_by_user_id + last_active_at + deleted_at.
    """

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    phone_masked: str | None = None
    email_masked: str | None = None
    nickname: str | None = None
    avatar_url: str | None = None
    region: str
    invite_code: str
    invited_by_user_id: uuid.UUID | None = Field(
        default=None,
        description="谁邀请来的; NULL = 无邀请人 (直接注册)",
    )
    is_admin: bool
    status: int
    is_deleted: bool
    deleted_at: datetime | None = None
    last_active_at: datetime
    created_at: datetime
    # 衍生 — 聚合查询
    invite_count: int = Field(
        ...,
        ge=0,
        description="该用户邀请的人数 (统计 users.invited_by = 本 user_id 且未软删)",
    )
    vip_status: str | None = None
    vip_plan: str | None = None
    vip_start_at: datetime | None = None
    vip_end_at: datetime | None = None
    vip_total_paid_cny: str | None = Field(
        default=None,
        description="累计支付 CNY (Decimal 序列化为字符串避免 JSON 精度丢失)",
    )


class AdminUserUpdate(BaseModel):
    """``PATCH /admin/users/{id}`` 请求体.

    安全收口 (用户拍板 Sprint 10 BE-S10-004 §安全):
    - **只能改** nickname / region / status — 这三个不破坏 PII 凭据
    - **不能改** phone / email / wechat_openid / apple_id — 凭据级修改必须走单独流程,
      防止 admin 误改让用户无法登录
    - **不能改** is_admin — 防越权 (admin 给自己/小号加权); 后续 sprint 加 super_admin 时再开
    - status 限 1 / 0 / -1 三态, 不接其它值
    """

    nickname: str | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        description="昵称 (1-20 字, 中英文混算); 传空字符串会被 min_length 挡",
    )
    region: str | None = Field(
        default=None,
        min_length=2,
        max_length=8,
        description="区域代码 (CN/HK/US 等 ISO 3166-1 alpha-2)",
    )
    status: Literal[1, 0, -1] | None = Field(
        default=None,
        description="1=启用, 0=禁用 (用户能登录但被风控), -1=封禁 (登录 401)",
    )


class GrantVipRequest(BaseModel):
    """``POST /admin/users/{id}/grant-vip`` 请求体.

    幂等策略 (用户拍板): **非幂等** — 连续点 2 次 = 加 2N 天.
    FE 在 modal 二次确认时显示"加完后将变为 xxx" 让 admin 自己看清楚.

    安全:
    - days ≤ 365: 单次最多 1 年 (防误操作输 999999); 想加更多走多次操作 + audit
    - reason 必填 ≥ 2 字: 不允许空 reason; 写日志 + (Sprint 11) admin_audit_logs.action_meta
    """

    days: int = Field(
        ...,
        ge=1,
        le=365,
        description="加多少天 (1-365); 单次上限 365 天防误操作",
    )
    reason: str = Field(
        ...,
        min_length=2,
        max_length=200,
        description="加 VIP 理由 (强制填); 写入 logger + 后续 admin_audit_logs.action_meta",
    )


__all__ = [
    "AdminUserDetail",
    "AdminUserListItem",
    "AdminUserListResponse",
    "AdminUserUpdate",
    "GrantVipRequest",
]
