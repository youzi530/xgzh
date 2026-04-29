"""社区 UGC ORM (Sprint 6 BE-S6-005): Post / Comment / Like / Report.

spec/13 §主线 D - 用户发帖 + 评论 + 点赞 + 举报 + 审核流.

为什么不用反向 relationship
===========================
- ``post.comments`` / ``post.likes`` / ``comment.replies`` 等反向 relationship 在
  社区 feed 场景容易触发 N+1 lazy load (一次查 20 帖 → 20 次单独查 comments)
- 业务层用 explicit JOIN / IN-clause 一次查完 + 在内存组装更可控
- 计数已在主表冗余 (likes_count / comments_count), 列表展示不依赖 relationship

字段语义见 alembic 0014_community.py docstring.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin


class CommunityPost(Base, TimestampMixin):
    """用户发布的帖子主体."""

    __tablename__ = "community_posts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'published', 'rejected', 'deleted', 'hidden')",
            name="ck_posts_status",
        ),
        CheckConstraint(
            "visibility IN ('public', 'self_only')",
            name="ck_posts_visibility",
        ),
        CheckConstraint(
            "category IN ('general', 'ipo_discuss', 'experience')",
            name="ck_posts_category",
        ),
        CheckConstraint(
            "likes_count >= 0 AND comments_count >= 0 AND reports_count >= 0",
            name="ck_posts_counts_nonneg",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text(), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
        comment="pending / published / rejected / deleted / hidden",
    )
    visibility: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'public'"),
    )
    category: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'general'"),
    )
    related_ipo_code: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
    )
    likes_count: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        server_default=text("0"),
    )
    comments_count: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        server_default=text("0"),
    )
    reports_count: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        server_default=text("0"),
    )
    rejection_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="admin user id; soft-link",
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class CommunityComment(Base):
    """帖子下的评论 (支持 1 层 self-FK 二级评论, 不允许更深)."""

    __tablename__ = "community_comments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'published', 'rejected', 'deleted')",
            name="ck_comments_status",
        ),
        CheckConstraint("likes_count >= 0", name="ck_comments_likes_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    post_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_posts.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_comment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("community_comments.id", ondelete="CASCADE"),
        nullable=True,
        comment="self-FK; null = 一级评论",
    )
    content: Mapped[str] = mapped_column(Text(), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
    )
    likes_count: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        server_default=text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class CommunityLike(Base):
    """点赞记录 (post / comment 通用)."""

    __tablename__ = "community_likes"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "target_type",
            "target_id",
            name="uq_likes_user_target",
        ),
        CheckConstraint(
            "target_type IN ('post', 'comment')",
            name="ck_likes_target_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class CommunityReport(Base):
    """举报队列 (admin 审核 SLA 24h)."""

    __tablename__ = "community_reports"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('post', 'comment')",
            name="ck_reports_target_type",
        ),
        CheckConstraint(
            "reason IN ('spam', 'illegal', 'misleading', 'privacy', 'pornographic', 'other')",
            name="ck_reports_reason",
        ),
        CheckConstraint(
            "status IN ('pending', 'resolved', 'dismissed')",
            name="ck_reports_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    reporter_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text(), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
    )
    handled_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="admin user id; soft-link",
    )
    handled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
