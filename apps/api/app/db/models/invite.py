"""邀请码 ORM: InviteCode + InviteReward (BE-S5-005 audit).

InviteCode: 每个用户注册时自动生成一条 (code = users.invite_code), 由本表统一管理
使用次数、上限与有效期, 用于活动期 / KOL 渠道追踪.

InviteReward: BE-S5-005 邀请有礼 trigger 审计表; 同 (inviter, threshold) 只发一次,
单阈值幂等防重.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
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


class InviteReward(Base):
    """邀请有礼 audit (BE-S5-005).

    仅 ``created_at`` 字段, 不继承 TimestampMixin 的 ``updated_at`` (奖励记录不可改).
    UNIQUE (inviter_user_id, threshold_n) 在 alembic 0010 显式建; 同时让
    ``apply_invite_reward`` 走 INSERT ... ON CONFLICT DO NOTHING 防并发双触发.
    """

    __tablename__ = "invite_rewards"
    __table_args__ = (
        UniqueConstraint(
            "inviter_user_id",
            "threshold_n",
            name="uq_invite_rewards_inviter_threshold",
        ),
    )

    reward_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    inviter_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    threshold_n: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    vip_days_granted: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    successful_invitee_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=text("now()"),
    )
