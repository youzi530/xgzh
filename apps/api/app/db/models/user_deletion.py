"""用户注销审计 ORM (BE-S5-003): UserDeletion.

记录用户主动调 ``DELETE /api/v1/me`` 的请求 + 30d 后 cron 真删完成时刻 +
原因 / IP / UA. 不继承 ``TimestampMixin`` (audit 字段不可改), 用专门的
``requested_at`` / ``real_purge_at`` 替代.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserDeletion(Base):
    """注销账号 audit 表; 一用户一行 (UNIQUE user_id).

    - ``real_purge_at IS NULL`` ⇔ 还在 30d 宽限期, cron 待跑
    - ``real_purge_at IS NOT NULL`` ⇔ cron 已真删 PII, 仅审计行保留
    """

    __tablename__ = "user_deletions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_deletions_user_id"),
    )

    deletion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    real_purge_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
    )
    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
    )
