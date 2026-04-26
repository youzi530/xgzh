"""邀请码 ORM: InviteCode.

每个用户注册时自动生成一条 (code = users.invite_code), 由本表统一管理
使用次数、上限与有效期, 用于活动期 / KOL 渠道追踪。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin


class InviteCode(Base, TimestampMixin):
    __tablename__ = "invite_codes"
    __table_args__ = (
        Index("ix_invite_codes_owner_user_id", "owner_user_id"),
        Index("ix_invite_codes_is_active", "is_active"),
    )

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        comment="代码所有人; 系统活动码可为 NULL",
    )
    usage_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    max_usage: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="NULL = 无上限",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    note: Mapped[str | None] = mapped_column(String(128), nullable=True)
