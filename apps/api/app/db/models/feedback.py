"""反馈 ORM: Feedback (Sprint 5 BE-S5-004).

最轻量收集: 用户提交一行 → admin 在面板里读. 不上工单状态机.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    ForeignKey,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin


class Feedback(Base, TimestampMixin):
    """用户反馈条目.

    继承 ``TimestampMixin`` 拿 ``created_at`` / ``updated_at`` (反馈不可改, 但
    mixin 一致性 > 微优化); 不继承 ``SoftDeleteMixin`` — 反馈是审计数据, 不软删,
    admin 真删 = DELETE.

    索引在 alembic 0009 用裸 SQL 创建 (DESC NULLS / partial 等 SQLAlchemy 不好直白
    表达), 这里 ORM 层不再重复声明.
    """

    __tablename__ = "feedbacks"

    feedback_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        comment="匿名 NULL; 用户注销后 SET NULL 脱钩",
    )
    category: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="bug / feature / content / other",
    )
    content: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
    )
    contact: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="phone / email / 微信号 (Sprint 5 无格式校验)",
    )
    app_version: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    platform: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="h5 / mp-weixin / app-android / app-ios",
    )
    ip_inet: Mapped[str | None] = mapped_column(
        INET(),
        nullable=True,
        comment="客户端 IP (PIPL 90d 留存)",
    )
