"""VIP 域 ORM (BE-S3-009): 订阅 + 订单 双表.

VipMembership / VipOrder 是 Sprint 3 商业化通道的底座: BE-S3-009 (状态机 + 7d
试用) + BE-S3-010 (微信支付 v3 + 回调) + agent.quota._resolve_plan (配额接真表)
全部读写这两张表.

总体设计
========
- 主键 ``membership_id`` / ``order_id`` UUID, ``gen_random_uuid()`` (与
  Sprint 1/2/3 风格一致).
- ``vip_memberships`` 一对一 ``users``: ``UNIQUE(user_id)`` 强制 — 1 用户 1 订阅
  关系, 续费走"覆盖 / 堆叠" (start_at / end_at 直接更新), 不开新行.
- ``vip_orders`` 多笔 (一对多): 试用授予 = 一笔 ``amount_cny=0,
  payment_channel='internal'`` 订单 (避免业务层试用 / 续费分支); 续费 = 新一笔
  ``status='paid'`` 订单, ``vip_memberships.current_order_id`` 指过去.
- ``vip_memberships.current_order_id`` 为 ``ON DELETE SET NULL`` 软关联: 订单被
  人为软删 / 退款时不破坏会员主表; 主链路通过 vip_orders 倒推订单历史.
- ``out_trade_no`` UNIQUE NOT NULL: 商户订单号, BE-S3-010 微信支付回调用作幂等
  键 — 同 out_trade_no 二次回调不重复处理.

外键级联策略
============
- ``vip_memberships.user_id`` → ``users.user_id`` ``ON DELETE CASCADE``
  (注销 = 删订阅; 与 ``user_favorites`` 一致, 因为订阅是用户独占数据)
- ``vip_memberships.current_order_id`` → ``vip_orders.order_id``
  ``ON DELETE SET NULL`` (订单可被运营软删, 不破坏会员主表)
- ``vip_orders.user_id`` → ``users.user_id`` ``ON DELETE CASCADE``
  (注销 = 删订单; 与 chat_sessions 不同, 因为订单是私密支付数据, 用户注销应彻底清)

写入顺序 (注册流程)
==================
service 层先写 ``vip_orders(plan='trial', amount_cny=0, status='paid',
payment_channel='internal')``, 再写 ``vip_memberships(current_order_id=order.id)``,
让 FK 自然有值. alembic 创建顺序: vip_orders → vip_memberships (FK 引用方向
要求 vip_orders 先存在).

索引设计 (5 个二级索引 + 2 个 UNIQUE)
====================================
- ``vip_memberships(user_id)`` UNIQUE                   — 一对一
- ``vip_memberships(status, end_at)``                   — scheduler 跑 expire_overdue
- ``vip_memberships(end_at)``                           — 到期监控 (近 7d 提醒)
- ``vip_orders(out_trade_no)`` UNIQUE                   — 微信回调幂等键
- ``vip_orders(user_id, created_at DESC)``              — 用户订单历史 / 我的订单页
- ``vip_orders(status, created_at)``                    — 待支付 / 失败重试列表
- ``vip_orders(payment_channel, created_at)``           — 渠道分账 / 财务对账
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin


class VipOrder(Base, TimestampMixin):
    """VIP 订阅订单 (一对多 ``users``).

    BE-S3-009 注册时写一笔 ``plan='trial', amount_cny=0,
    payment_channel='internal', status='paid'`` 的零元订单 = 试用入口
    (避免业务层试用 / 付费分支).

    BE-S3-010 微信支付落地后, 真支付订单写 ``payment_channel='wechat_mp'``,
    ``out_trade_no = "XGZH" + timestamp + 4-byte rand``, ``status='pending'``;
    回调成功 → ``status='paid'`` + 回填 ``transaction_id`` / ``paid_at`` /
    ``raw_callback``.

    退款: ``status='refunded'`` (未实施 — Sprint 4+ 财务对账后台再做).
    """

    __tablename__ = "vip_orders"
    __table_args__ = (
        UniqueConstraint("out_trade_no", name="uq_vip_orders_out_trade_no"),
        Index(
            "ix_vip_orders_user_created",
            "user_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_vip_orders_status_created",
            "status",
            "created_at",
        ),
        Index(
            "ix_vip_orders_payment_channel_created",
            "payment_channel",
            "created_at",
        ),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    out_trade_no: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="商户订单号; BE-S3-010 用作微信支付回调幂等键",
    )
    plan: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="trial / monthly / quarterly / yearly / lifetime",
    )
    amount_cny: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        comment="订单金额 CNY; 试用 = 0.00",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
        comment="pending / paid / failed / refunded",
    )
    payment_channel: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="wechat_mp / wechat_h5 / apple_iap / internal (试用零元单)",
    )
    transaction_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="微信支付单号 / Apple IAP transaction id; 回调时回填",
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="支付完成时间; status='paid' 时回填",
    )
    raw_callback: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="验签后的完整回调 payload, 审计 / 排错用",
    )


class VipMembership(Base, TimestampMixin):
    """用户 VIP 订阅主表 (一对一 ``users``).

    续费状态机 (BE-S3-009 §关键设计):
    - 现 status ∈ (trialing, expired, cancelled) → 直接覆盖 start_at / end_at
    - 现 status='active'                          → end_at += plan_duration (堆叠续费)

    试用授予 (注册成功后): start_at=now, end_at=now+7d, status='trialing',
    plan='trial', total_paid_cny=0, current_order_id 指向零元订单.

    Lifetime 订阅: end_at = ``9999-12-31`` (避免业务层 ``end_at IS NULL`` 分支;
    与 ``invite_codes.expires_at IS NULL`` 不同思路, 因为这里 end_at NOT NULL).

    Scheduler ``expire_overdue_memberships()`` 每 1h 跑: ``UPDATE
    vip_memberships SET status='expired' WHERE status IN ('trialing','active')
    AND end_at < now()``. 走 ``ix_vip_memberships_status_end_at``.
    """

    __tablename__ = "vip_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_vip_memberships_user_id"),
        Index(
            "ix_vip_memberships_status_end_at",
            "status",
            "end_at",
        ),
        Index(
            "ix_vip_memberships_end_at",
            "end_at",
        ),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        comment="一对一; UNIQUE 约束 + CASCADE (注销 = 删订阅)",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="trialing / active / expired / cancelled",
    )
    plan: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="trial / monthly / quarterly / yearly / lifetime",
    )
    start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    end_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="lifetime 设为 9999-12-31; 避免 NULL 分支",
    )
    auto_renew: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="iOS IAP 默认 true, 微信支付不支持订阅, 默认 false",
    )
    current_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vip_orders.order_id", ondelete="SET NULL"),
        nullable=True,
        comment="指向最近一笔成功订单 (试用 / 续费); 订单软删时 SET NULL",
    )
    total_paid_cny: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        nullable=False,
        server_default=text("0"),
        comment="累计支付 CNY; BE-S3-010 回调成功时累加",
    )
