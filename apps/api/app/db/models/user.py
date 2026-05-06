"""用户域 ORM: User / UserFavorite.

字段命名严格对齐 ``spec/05-全栈技术栈选型.md §3.2`` 的 ``users`` 表设计,
便于后续 Sprint 增量演进 (vip_memberships / wallet 等) 时不需改字段。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models._mixins import SoftDeleteMixin, TimestampMixin


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    # ⚠️ ``uq_users_email`` 是 partial unique (PG WHERE email IS NOT NULL),
    # 在 alembic/0016 用 raw SQL 创建; 这里不在 __table_args__ 重复声明,
    # 避免 SQLAlchemy autogenerate 时把 partial 退化成 full UNIQUE 误判 diff.
    __table_args__ = (
        UniqueConstraint("phone", name="uq_users_phone"),
        UniqueConstraint("wechat_openid", name="uq_users_wechat_openid"),
        UniqueConstraint("apple_id", name="uq_users_apple_id"),
        UniqueConstraint("invite_code", name="uq_users_invite_code"),
        Index("ix_users_wechat_unionid", "wechat_unionid"),
        Index("ix_users_status", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # BUG-S9-001 邮箱凭据. RFC 5321 max 254 字符. 后端 normalize 成小写,
    # partial unique (alembic 0016) 保证非 NULL 时全局唯一.
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    # BUG-S9-001 bcrypt 密码 hash. 固定 60 字符 ($2b$12$<22 salt><31 hash>).
    # NULL = 用户尚未设置密码 (首次密码登录前走 PUT /me/password).
    password_hash: Mapped[str | None] = mapped_column(String(60), nullable=True)
    wechat_openid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wechat_unionid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    apple_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    region: Mapped[str] = mapped_column(String(8), nullable=False, server_default="CN")
    invite_code: Mapped[str] = mapped_column(String(16), nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("1"),
        comment="1=active, 0=disabled, -1=banned",
    )
    last_active_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )

    favorites: Mapped[list[UserFavorite]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class UserFavorite(Base, TimestampMixin):
    """用户收藏的 IPO（按 code+market 复合主键）."""

    __tablename__ = "user_favorites"
    __table_args__ = (
        Index("ix_user_favorites_ipo_code_market", "ipo_code", "market"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    ipo_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    market: Mapped[str] = mapped_column(String(4), primary_key=True)
    notify_on_subscribe: Mapped[bool] = mapped_column(
        nullable=False,
        server_default=text("true"),
    )

    user: Mapped[User] = relationship(back_populates="favorites")
