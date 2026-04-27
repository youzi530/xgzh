"""add vip tables (Sprint 3 BE-S3-009):
vip_orders + vip_memberships + 5 indexes.

Revision ID: 0007_vip
Revises: 0006_brokers
Create Date: 2026-04-27

背景
====
Sprint 3 三张表打包同日落定的最后一张 (0005 articles → 0006 brokers →
本 0007 vip), 一次性消除 alembic head 漂移. 之后 BE-S3-009 (状态机 + 7d 试用) /
BE-S3-010 (微信支付 v3 回调) / FE-S3-006 (订阅升级页) / agent.quota
``_resolve_plan`` (配额接真表) 全部在这两张表上读写.

设计要点
========
1. **建表顺序: vip_orders 先, vip_memberships 后**
   ``vip_memberships.current_order_id`` FK 引用 ``vip_orders.order_id``,
   alembic 必须先有 vip_orders. 反向亦然: downgrade 先删 vip_memberships
   (drop FK 后), 再删 vip_orders.

2. **vip_memberships 一对一 users**: ``UNIQUE(user_id)`` 物理保证.
   续费走"覆盖 / 堆叠" (start_at / end_at 直接 UPDATE), 不开新行 — 业务读
   永远只查 1 行 ``WHERE user_id = ?``.

3. **vip_orders 一对多 users**: 试用授予 = 一笔零元订单
   (``amount_cny=0, payment_channel='internal', status='paid'``), 避免
   service 层试用 / 付费分支 (BE-S3-009 §关键设计).

4. **out_trade_no UNIQUE**: BE-S3-010 微信支付回调幂等键. 同 out_trade_no
   二次回调 → SDK 验签后查到已是 ``status='paid'`` → 直接返 SUCCESS, 不重复
   流转 vip_memberships.

5. **end_at NOT NULL + lifetime = 9999-12-31**: 避免业务层 ``end_at IS NULL``
   分支 (与 ``invite_codes.expires_at IS NULL`` 不同思路, 此处 end_at 业务上
   一定有值).

6. **外键级联策略**:
   * ``vip_memberships.user_id → users`` ``ON DELETE CASCADE``
   * ``vip_memberships.current_order_id → vip_orders`` ``ON DELETE SET NULL``
     (订单可被运营软删, 不破坏会员主表; 主链路通过 vip_orders 倒推订单历史)
   * ``vip_orders.user_id → users`` ``ON DELETE CASCADE`` (订单是私密支付数据,
     用户注销 = 彻底清, 不留历史)

索引设计 (5 个二级 + 2 个 UNIQUE)
================================
- ``vip_memberships(user_id)`` UNIQUE                    — 一对一
- ``vip_memberships(status, end_at)``                    — scheduler expire job
  (``UPDATE WHERE status IN ('trialing','active') AND end_at < now()``)
- ``vip_memberships(end_at)``                            — 到期监控 (近 7d 提醒)
- ``vip_orders(out_trade_no)`` UNIQUE                    — 微信回调幂等键
- ``vip_orders(user_id, created_at DESC)``               — 用户订单历史
- ``vip_orders(status, created_at)``                     — 待支付列表
- ``vip_orders(payment_channel, created_at)``            — 渠道分账 / 财务对账

回滚策略
========
``downgrade()``: vip_memberships 先删 (依赖 vip_orders FK), vip_orders 后删.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0007_vip"
down_revision: str | None = "0006_brokers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- vip_orders (先建, vip_memberships.current_order_id FK 引用) ---
    op.create_table(
        "vip_orders",
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "users.user_id",
                ondelete="CASCADE",
                name="fk_vip_orders_user_id_users",
            ),
            nullable=False,
        ),
        sa.Column(
            "out_trade_no",
            sa.String(64),
            nullable=False,
            comment="商户订单号; BE-S3-010 用作微信支付回调幂等键",
        ),
        sa.Column(
            "plan",
            sa.String(16),
            nullable=False,
            comment="trial / monthly / quarterly / yearly / lifetime",
        ),
        sa.Column(
            "amount_cny",
            sa.Numeric(10, 2),
            nullable=False,
            comment="订单金额 CNY; 试用 = 0.00",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'pending'"),
            comment="pending / paid / failed / refunded",
        ),
        sa.Column(
            "payment_channel",
            sa.String(16),
            nullable=False,
            comment="wechat_mp / wechat_h5 / apple_iap / internal (试用零元单)",
        ),
        sa.Column(
            "transaction_id",
            sa.String(64),
            nullable=True,
            comment="微信支付单号 / Apple IAP transaction id; 回调时回填",
        ),
        sa.Column(
            "paid_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="支付完成时间; status='paid' 时回填",
        ),
        sa.Column(
            "raw_callback",
            JSONB,
            nullable=True,
            comment="验签后的完整回调 payload, 审计 / 排错用",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("out_trade_no", name="uq_vip_orders_out_trade_no"),
    )
    op.execute(
        """
        CREATE INDEX ix_vip_orders_user_created
        ON vip_orders (user_id, created_at DESC);
        """
    )
    op.create_index(
        "ix_vip_orders_status_created",
        "vip_orders",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_vip_orders_payment_channel_created",
        "vip_orders",
        ["payment_channel", "created_at"],
    )

    # --- vip_memberships (后建, 一对一 users; current_order_id 软引用 vip_orders) ---
    op.create_table(
        "vip_memberships",
        sa.Column(
            "membership_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "users.user_id",
                ondelete="CASCADE",
                name="fk_vip_memberships_user_id_users",
            ),
            nullable=False,
            comment="一对一; UNIQUE 约束 + CASCADE (注销 = 删订阅)",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            comment="trialing / active / expired / cancelled",
        ),
        sa.Column(
            "plan",
            sa.String(16),
            nullable=False,
            comment="trial / monthly / quarterly / yearly / lifetime",
        ),
        sa.Column(
            "start_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "end_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="lifetime 设为 9999-12-31; 避免 NULL 分支",
        ),
        sa.Column(
            "auto_renew",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
            comment="iOS IAP 默认 true, 微信支付不支持订阅, 默认 false",
        ),
        sa.Column(
            "current_order_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "vip_orders.order_id",
                ondelete="SET NULL",
                name="fk_vip_memberships_current_order_id_vip_orders",
            ),
            nullable=True,
            comment="指向最近一笔成功订单 (试用 / 续费); 订单软删时 SET NULL",
        ),
        sa.Column(
            "total_paid_cny",
            sa.Numeric(10, 2),
            nullable=False,
            server_default=sa.text("0"),
            comment="累计支付 CNY; BE-S3-010 回调成功时累加",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id",
            name="uq_vip_memberships_user_id",
        ),
    )
    op.create_index(
        "ix_vip_memberships_status_end_at",
        "vip_memberships",
        ["status", "end_at"],
    )
    op.create_index(
        "ix_vip_memberships_end_at",
        "vip_memberships",
        ["end_at"],
    )


def downgrade() -> None:
    # 反向: vip_memberships 先删 (依赖 vip_orders FK), vip_orders 后删.
    op.drop_table("vip_memberships")
    op.drop_table("vip_orders")
