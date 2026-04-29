"""社区 Pydantic schemas (Sprint 6 BE-S6-006/007/008/009).

请求 / 响应模型对齐 spec/13 §主线 D + alembic 0014_community.

设计要点:
- ``content`` 字段 server-side 长度限制 (post=500, comment=200) 用 Pydantic Field
  约束; 业务层不重复校验
- 列表 / 详情区分: 列表轻量 (不带评论列表), 详情含 ``user_nickname`` join
- 状态 / 分类 / 拒绝原因等都走 ``Literal`` 严格枚举
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

PostStatus = Literal["pending", "published", "rejected", "deleted", "hidden"]
PostVisibility = Literal["public", "self_only"]
PostCategory = Literal["general", "ipo_discuss", "experience"]
CommentStatus = Literal["pending", "published", "rejected", "deleted"]
LikeTargetType = Literal["post", "comment"]
ReportReason = Literal[
    "spam", "illegal", "misleading", "privacy", "pornographic", "other"
]
ReportStatus = Literal["pending", "resolved", "dismissed"]
RejectionReason = Literal[
    "content_violation", "privacy_leak", "spam", "other"
]


# ─── Post ──────────────────────────────────────────────────────────────


class PostCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=500, description="帖子内容")
    category: PostCategory = "general"
    related_ipo_code: str | None = Field(default=None, max_length=16)


class PostDetailResponse(BaseModel):
    """帖子详情 (含 user 冗余字段, 列表 / 详情共用)."""

    id: uuid.UUID
    user_id: uuid.UUID
    user_nickname: str | None = None
    user_avatar_url: str | None = None
    content: str
    status: PostStatus
    visibility: PostVisibility
    category: PostCategory
    related_ipo_code: str | None = None
    likes_count: int
    comments_count: int
    reports_count: int
    rejection_reason: RejectionReason | None = None
    is_liked: bool = Field(default=False, description="当前用户是否已赞")
    created_at: datetime
    updated_at: datetime


class PostListResponse(BaseModel):
    items: list[PostDetailResponse]
    total: int
    page: int
    page_size: int


# ─── Comment ──────────────────────────────────────────────────────────


class CommentCreateRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=200)
    parent_comment_id: uuid.UUID | None = Field(
        default=None, description="二级评论的父评论 id; 为空 = 一级评论"
    )


class CommentResponse(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID
    user_id: uuid.UUID
    user_nickname: str | None = None
    user_avatar_url: str | None = None
    parent_comment_id: uuid.UUID | None = None
    content: str
    status: CommentStatus
    likes_count: int
    is_liked: bool = False
    created_at: datetime


class CommentListResponse(BaseModel):
    items: list[CommentResponse]
    total: int


# ─── Like ──────────────────────────────────────────────────────────────


class LikeRequest(BaseModel):
    target_type: LikeTargetType
    target_id: uuid.UUID


class LikeResponse(BaseModel):
    target_type: LikeTargetType
    target_id: uuid.UUID
    liked: bool
    likes_count: int


# ─── Report ────────────────────────────────────────────────────────────


class ReportRequest(BaseModel):
    target_type: LikeTargetType
    target_id: uuid.UUID
    reason: ReportReason
    detail: str | None = Field(default=None, max_length=500)


class ReportResponse(BaseModel):
    id: uuid.UUID
    target_type: LikeTargetType
    target_id: uuid.UUID
    reason: ReportReason
    status: ReportStatus
    created_at: datetime
