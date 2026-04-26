"""推送 token ORM: PushToken.

支持 iOS/Android/微信小程序模板消息/H5 浏览器推送, 同一用户同一设备
的 token 唯一 (device_id 可空, 适配 H5 场景)。
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin


class PushToken(Base, TimestampMixin):
    __tablename__ = "push_tokens"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "platform", "device_id", name="uq_push_tokens_user_platform_device"
        ),
        Index("ix_push_tokens_user_id", "user_id"),
        Index("ix_push_tokens_is_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="ios/android/wxmp/h5",
    )
    token: Mapped[str] = mapped_column(Text, nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
