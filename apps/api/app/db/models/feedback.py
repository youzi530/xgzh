"""反馈 ORM: Feedback (Sprint 5 BE-S5-004 + Sprint 11 BE-S11-B01 admin 工作流).

最轻量收集 (Sprint 5): 用户提交一行 → admin 读. 不上工单状态机.

Sprint 11 加 admin 处理工作流字段:
- ``admin_status`` 处理状态 (pending/reviewed/resolved/closed; NULL = 等同 pending)
- ``admin_note`` admin 间内部备注 (用户看不到)
- ``reviewed_by`` / ``reviewed_at`` 谁/啥时候处理的
- ``deleted_at`` 软删 (跟其它表统一; 30d 后 cron 硬删)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import SoftDeleteMixin, TimestampMixin


class Feedback(Base, TimestampMixin, SoftDeleteMixin):
    """用户反馈条目.

    继承 ``TimestampMixin`` (created_at / updated_at) + ``SoftDeleteMixin``
    (deleted_at, Sprint 11 加上去). admin 软删 → ``deleted_at = NOW()``,
    30 天后 cron 硬删 (PIPL 最小化).

    Sprint 5 ``list_feedbacks`` 走 ``X-Admin-Token`` ops 路径, 当时不暴露 deleted_at
    (因为没有). Sprint 11 加 JWT in-app 路径 ``/admin/feedbacks`` 默认隐藏 deleted_at
    NOT NULL 的行, ops 路径行为不变 (兼容 ops 旧脚本).

    索引在 alembic 0009 用裸 SQL 创建 (DESC NULLS / partial), ORM 层不重复声明.
    admin 工作流字段量级低不加额外索引.
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

    # ─── Sprint 11 BE-S11-B01 admin 工作流字段 ──────────────────────
    admin_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        comment=(
            "admin 处理状态: pending / reviewed / resolved / closed; "
            "NULL = 没人看过 (等同 pending)"
        ),
    )
    admin_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="admin 内部备注 (admin 间协作; 不暴露给用户)",
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        comment="处理人 admin user_id; 注销时 SET NULL",
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="处理时间戳 (跟 reviewed_by 一起填/清)",
    )
