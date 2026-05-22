"""反馈域 Pydantic schemas (Sprint 5 BE-S5-004).

字段语义对齐 ``app.db.models.Feedback`` ORM + spec/12 §BE-S5-004.
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Literal 让应用层校验 + OpenAPI schema 自动产出枚举值, 比 PG enum 更轻
FeedbackCategory = Literal["bug", "feature", "content", "other"]
FeedbackPlatform = Literal["h5", "mp-weixin", "app-android", "app-ios"]


class FeedbackCreateRequest(BaseModel):
    """``POST /api/v1/feedback`` 请求体."""

    category: FeedbackCategory = Field(..., description="bug / feature / content / other")
    content: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="反馈正文; 1 ~ 2000 字 (含标点)",
    )
    contact: str | None = Field(
        default=None,
        max_length=64,
        description="可选: 用户留 phone / email / 微信号 — 让客服回拨; 不做格式校验",
    )
    app_version: str | None = Field(
        default=None,
        max_length=32,
        description="客户端版本号; FE 自带",
    )
    platform: FeedbackPlatform = Field(
        ..., description="h5 / mp-weixin / app-android / app-ios"
    )


class FeedbackCreateResponse(BaseModel):
    """``POST /api/v1/feedback`` 响应."""

    feedback_id: uuid.UUID = Field(..., description="服务端生成的反馈 ID")
    created_at: datetime = Field(..., description="收到时间 (UTC)")


class FeedbackAdminItem(BaseModel):
    """``GET /api/v1/admin/feedbacks`` 列表项 (admin 视角, 含 user_id / IP)."""

    model_config = ConfigDict(from_attributes=True)

    feedback_id: uuid.UUID
    user_id: uuid.UUID | None
    category: FeedbackCategory
    content: str
    contact: str | None
    app_version: str | None
    platform: FeedbackPlatform
    ip_inet: str | None = Field(
        default=None,
        description="客户端 IP — admin 排查滥用 / 复线问题用",
    )
    created_at: datetime

    @field_validator("ip_inet", mode="before")
    @classmethod
    def _coerce_ip_to_str(cls, v: Any) -> str | None:
        """PG ``INET`` 列在 asyncpg 里读出来是 ``IPv4Address`` / ``IPv6Address``
        对象, Pydantic 默认不认; 这里统一 str() 再校验. None / 已经是 str 透传.
        """
        if v is None:
            return None
        if isinstance(v, ipaddress.IPv4Address | ipaddress.IPv6Address):
            return str(v)
        return v  # type: ignore[no-any-return]


class FeedbackAdminListResponse(BaseModel):
    """admin 列表分页响应 (老 ops 路径用; limit/offset)."""

    items: list[FeedbackAdminItem]
    total: int = Field(..., description="符合 filter 条件的总数 (用于分页)")
    limit: int = Field(..., description="本页 limit")
    offset: int = Field(..., description="本页 offset")


# ─── Sprint 11 BE-S11-B02: admin JWT 路径 schemas ─────────────────────

AdminFeedbackStatus = Literal["pending", "reviewed", "resolved", "closed"]


class AdminFeedbackListItem(BaseModel):
    """JWT 路径 ``/admin/feedbacks`` 列表项. 比老 ``FeedbackAdminItem`` 多 admin 字段."""

    model_config = ConfigDict(from_attributes=True)

    feedback_id: uuid.UUID
    user_id: uuid.UUID | None
    category: FeedbackCategory
    content: str
    contact: str | None
    app_version: str | None
    platform: FeedbackPlatform
    ip_inet: str | None = Field(default=None)
    created_at: datetime
    # Sprint 11 加
    admin_status: AdminFeedbackStatus | None = Field(
        default=None,
        description="处理状态; NULL = 还没看过 (等同 pending)",
    )
    admin_note: str | None = None
    reviewed_by: uuid.UUID | None = None
    reviewed_at: datetime | None = None
    deleted_at: datetime | None = None
    is_deleted: bool = False

    @field_validator("ip_inet", mode="before")
    @classmethod
    def _coerce_ip_to_str(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, ipaddress.IPv4Address | ipaddress.IPv6Address):
            return str(v)
        return v  # type: ignore[no-any-return]


class AdminFeedbackDetail(AdminFeedbackListItem):
    """JWT 路径 ``/admin/feedbacks/{id}`` 详情. 字段与列表项一致 (反馈本身字段少)."""


class AdminFeedbackListResponse(BaseModel):
    """JWT 路径分页响应 (page/page_size; 跟 admin_users 一致)."""

    model_config = ConfigDict(extra="forbid")

    items: list[AdminFeedbackListItem]
    total: int = Field(ge=0)
    page: int
    page_size: int


class AdminFeedbackUpdate(BaseModel):
    """``PATCH /admin/feedbacks/{id}``. 全字段可选 (None = 不动)."""

    model_config = ConfigDict(extra="forbid")

    admin_status: AdminFeedbackStatus | None = Field(
        default=None,
        description="改处理状态; 自动填 reviewed_by / reviewed_at",
    )
    admin_note: str | None = Field(
        default=None,
        max_length=2000,
        description="admin 内部备注; 空字符串 = 清备注",
    )


__all__ = [
    "AdminFeedbackDetail",
    "AdminFeedbackListItem",
    "AdminFeedbackListResponse",
    "AdminFeedbackStatus",
    "AdminFeedbackUpdate",
    "FeedbackAdminItem",
    "FeedbackAdminListResponse",
    "FeedbackCategory",
    "FeedbackCreateRequest",
    "FeedbackCreateResponse",
    "FeedbackPlatform",
]
