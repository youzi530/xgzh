"""中签记账 ORM (Sprint 6 BE-S6-001): SubscriptionAccount + SubscriptionRecord.

spec/13 §主线 B - 用户在自己券商 APP 看到中签后, 把记录手动录入 XGZH 统一记账.
不实现"按证件号自动查中签" (Spike-1 路径不通) — 用户主动录入是 MVP 唯一方案.

设计说明
========
两表分开:
- ``SubscriptionAccount`` 多账户元数据 (label / broker_name / region / is_primary)
- ``SubscriptionRecord`` 单条中签 / 申购记录 (一条 = 一只 IPO × 一个账户)

不写反向 ORM relationship: ``account.records`` 用 join 即可 (业务层 SELECT WHERE
account_id=xxx 简单清晰), 反向 relationship 在中签 tab 主页"按账户列 records"
场景没显著优势, 反而增加 lazy-load N+1 风险.

关系字段全用 ForeignKey + ON DELETE CASCADE:
- 用户注销 → 全部 accounts + records 一并清 (PIPL 30d 真删)
- 删账户 → 该账户 records 一并清 (UI 二次确认 + DB 强一致)

详细字段语义见 alembic 0012_subscriptions.py 的 docstring.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CHAR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin


class SubscriptionAccount(Base):
    """用户中签记账的"账户"主体 (一用户多账户; 跨券商打新场景).

    不继承 ``TimestampMixin``: 账户元数据只关心 ``created_at`` (审计),
    不需要 ``updated_at`` — 改 label 这种轻量更新不必维护时间戳.
    """

    __tablename__ = "subscription_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", "label", name="uq_sub_accounts_user_label"),
        CheckConstraint("region IN ('HK', 'CN', 'US')", name="ck_sub_accounts_region"),
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
    label: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="用户起的账户名, 32 char 上限",
    )
    broker_name: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="optional 真券商名 ('招商证券' / '华盛证券')",
    )
    region: Mapped[str] = mapped_column(
        CHAR(2),
        nullable=False,
        server_default=text("'HK'"),
        comment="HK / CN / US",
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default=text("false"),
        comment="切换器默认选中标记; 业务层维护单一",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class SubscriptionRecord(Base, TimestampMixin):
    """单条中签 / 申购记录.

    继承 ``TimestampMixin``: ``updated_at`` 跟踪用户后续编辑 (例如先录入未中签
    再补卖出价 / 修中签数), 业务层重算 PnL 时方便 audit.
    """

    __tablename__ = "subscription_records"
    __table_args__ = (
        CheckConstraint("subscribe_shares > 0", name="ck_sub_records_subscribe_pos"),
        CheckConstraint("allotted_shares >= 0", name="ck_sub_records_allotted_nonneg"),
        CheckConstraint(
            "allotted_shares <= subscribe_shares",
            name="ck_sub_records_allotted_le_subscribe",
        ),
        CheckConstraint("region IN ('HK', 'CN', 'US')", name="ck_sub_records_region"),
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
        comment="冗余于 account.user_id, 方便 user 维度查询不必 join",
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscription_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    ipo_code: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="不 FK ipos.code; 业务层做 soft-link 校验",
    )
    ipo_name: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    region: Mapped[str] = mapped_column(
        CHAR(2),
        nullable=False,
    )
    subscribe_shares: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
    )
    allotted_shares: Mapped[int] = mapped_column(
        Integer(),
        nullable=False,
        server_default=text("0"),
        comment="0 = 未中签",
    )
    subscribe_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
        nullable=True,
    )
    margin_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2),
        nullable=True,
        comment="港股孖展利息 (A 股 NULL)",
    )
    fees: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        server_default=text("0"),
    )
    first_day_close: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
        nullable=True,
    )
    sell_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
        nullable=True,
        comment="NULL = 还持有",
    )
    sell_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    realized_pnl: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2),
        nullable=True,
        comment="业务层算后存盘",
    )
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2),
        nullable=True,
        comment="业务层算后存盘 (按 first_day_close)",
    )
    notes: Mapped[str | None] = mapped_column(
        Text(),
        nullable=True,
    )
    subscribed_at: Mapped[date] = mapped_column(
        Date(),
        nullable=False,
    )
    listed_at: Mapped[date | None] = mapped_column(
        Date(),
        nullable=True,
    )
