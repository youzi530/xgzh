"""add broker tables (Sprint 3 BE-S3-007 / 008):
brokers + conversion_events + 6 indexes.

Revision ID: 0006_brokers
Revises: 0005_articles
Create Date: 2026-04-27

背景
====
Sprint 3 三张表打包同日落定 (BE-S3-001 / 007 / 009) 一次性消除 alembic head
漂移: 0005 articles 已落, 本 migration 同时建 brokers + conversion_events 两张
变现侧表 (spec/10 §BE-S3-008 §"改动文件"明确 alembic head 同一版本).

后续业务侧 PR (BE-S3-007 seeds / service / API + BE-S3-008 redirect 端点 +
Sprint 4+ 财务对账) 全部在这两张表上读写.

设计要点
========
1. **JSONB 重场字段**: brokers 7 个核心业务字段 (market_support / licenses /
   fees / features / promotion + display_order / is_active 标量) — 各券商
   schema 不一, JSONB 直存比规范化拆 N 张子表收益高 (查询端走 ``model_dump()``
   + FE 渲染, 写入端 seeds 脚本一行 upsert).
   * server_default ``'[]'::jsonb`` / ``'{}'::jsonb``: 让 ``Broker(**partial)``
     的最小字段构造也能跑通, 不被 NOT NULL 卡 (写测试方便).

2. **slug UNIQUE**: ``/api/v1/brokers/{slug}`` 详情路由用 slug 而非 UUID
   (URL 友好); UNIQUE 物理保证.

3. **partnership_* 三字段同表**: ``BrokerInternal`` only 字段 (CPA / CPS 内部
   数据, 不直接出 API) — schema 隔离走 service / API 层 ``BrokerPublic.
   model_dump(include=...)``, DB 层都存, 不为内部字段拆出 ``broker_partnerships``
   子表 (1:1 子表 = 多一次 JOIN, 收益小).

4. **conversion_events append-only**: 不带 ``updated_at`` (与 ``chat_messages``
   / ``chat_token_usage`` 同), 写入即历史; 唯一可改字段 ``attributed`` (财务
   对账核销标志) 走 raw SQL UPDATE, 不通过 ORM session.dirty 触发.
   * ``device_id`` NOT NULL: 匿名跳转也强制有 device_id (前端拦截器自动注入,
     与 push_tokens.device_id 同语义); BE-S3-008 防刷 Redis key 用.
   * ``ip_addr INET``: PG 原生 INET 类型; 比 ``String(45)`` 更合适 (校验 +
     subnet 查询能力).

5. **外键级联策略**:
   * ``conversion_events.user_id → users`` ``ON DELETE SET NULL``: 注销不丢历史
     (与 ``invite_codes.owner_user_id`` 同思路, CPA / CPS 财务对账可追溯)
   * ``conversion_events.broker_id → brokers`` ``ON DELETE CASCADE``: 券商物理
     删 = 埋点全清 (生产应走 SoftDelete, 物理删极少见)
   * brokers 自身走 SoftDeleteMixin (deleted_at), 不暴露物理 DELETE 路径

索引设计 (6 个二级 + 2 个 UNIQUE)
================================
- ``brokers(slug)`` UNIQUE                                      — 详情页 URL 路由
- ``brokers(is_active, display_order)``                         — 列表 API 默认排序
  (运营常调 display_order, 不带 DESC; PG 9.6+ 索引扫描支持反向遍历, 等价于
  WHERE is_active=true ORDER BY display_order DESC LIMIT 20)
- ``conversion_events(broker_id, event_type, created_at DESC)`` — 券商 30d stats
- ``conversion_events(user_id, created_at DESC)``               — 用户行为追踪
- ``conversion_events(utm_campaign, created_at DESC)``          — 活动归因报表
- ``conversion_events(attributed, created_at)``                 — 待核销 CPS 列表
  (attributed=false 行很少, 索引体积可控)

回滚策略
========
``downgrade()``: conversion_events 先删 (依赖 brokers FK), brokers 后删. 索引
随 ``DROP TABLE`` 一起消失, 不需要单独 ``DROP INDEX``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID

from alembic import op

revision: str = "0006_brokers"
down_revision: str | None = "0005_articles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- brokers ---
    op.create_table(
        "brokers",
        sa.Column(
            "broker_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "name_zh",
            sa.String(64),
            nullable=False,
            comment="中文名 (主显), 如 '富途牛牛'",
        ),
        sa.Column(
            "name_en",
            sa.String(64),
            nullable=True,
            comment="英文名, 如 'Futu' (港股 / 国际化用)",
        ),
        sa.Column("logo_url", sa.Text, nullable=True),
        sa.Column(
            "slug",
            sa.String(32),
            nullable=False,
            comment="URL slug (futubull / tigerbrokers / longbridge); UNIQUE; FE 路由用",
        ),
        sa.Column(
            "market_support",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="支持市场 ['HK', 'A', 'US', 'SG']",
        ),
        sa.Column(
            "licenses",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="持牌列表 ['SFC-1', 'SFC-4', 'SEC']",
        ),
        sa.Column(
            "fees",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment=(
                "费率 { hk_commission_rate, hk_min_commission, "
                "a_commission_rate, platform_fee, margin_rate_hkd, cancel_fee }; "
                "各券商 schema 不一, JSONB 直存"
            ),
        ),
        sa.Column(
            "features",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment=(
                "功能 { ipo_subscription, dark_pool_trading, margin_trading, "
                "chinese_service, min_deposit_hkd } 五元组"
            ),
        ),
        sa.Column(
            "promotion",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment=(
                "活动 { is_active, title, description, end_at (ISO string), "
                "invite_code, referral_url }; end_at 用 string 而非 timestamp 列, "
                "减少 schema 变化成本"
            ),
        ),
        sa.Column(
            "partnership_type",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'NONE'"),
            comment="CPA / CPS / BOTH / NONE; 决定财务对账逻辑",
        ),
        sa.Column(
            "partnership_cpa_amount",
            sa.Numeric(10, 2),
            nullable=True,
            comment="每注册 1 用户返佣 CNY (CPA / BOTH 时填)",
        ),
        sa.Column(
            "partnership_cps_rate",
            sa.Numeric(6, 5),
            nullable=True,
            comment="入金 / 交易额分成比例 0.00000-1.00000 (CPS / BOTH 时填)",
        ),
        sa.Column(
            "display_order",
            sa.SmallInteger,
            nullable=False,
            server_default=sa.text("0"),
            comment="运营手动排序权重; 越大越靠前",
        ),
        sa.Column(
            "is_active",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
            comment="false = 暂时下架 (软隐藏); 与 deleted_at 区分: 后者是逻辑删除",
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
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint("slug", name="uq_brokers_slug"),
    )
    op.create_index(
        "ix_brokers_is_active_display_order",
        "brokers",
        ["is_active", "display_order"],
    )

    # --- conversion_events ---
    op.create_table(
        "conversion_events",
        sa.Column(
            "event_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "users.user_id",
                ondelete="SET NULL",
                name="fk_conversion_events_user_id_users",
            ),
            nullable=True,
            comment="可空: 匿名跳转也落埋点; 用户注销后 SET NULL 不丢历史",
        ),
        sa.Column(
            "device_id",
            sa.Text,
            nullable=False,
            comment="前端拦截器自动注入 (与 push_tokens.device_id 同语义)",
        ),
        sa.Column(
            "broker_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "brokers.broker_id",
                ondelete="CASCADE",
                name="fk_conversion_events_broker_id_brokers",
            ),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.String(16),
            nullable=False,
            comment="click / signup / kyc_pass / deposit / first_trade",
        ),
        sa.Column(
            "utm_source",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'xgzh'"),
            comment="默认 'xgzh'; 多入口时可标记 'xgzh-app' / 'xgzh-mp'",
        ),
        sa.Column(
            "utm_campaign",
            sa.String(64),
            nullable=True,
            comment="活动 ID (/redirect?utm_campaign=ipo-202604)",
        ),
        sa.Column("utm_medium", sa.String(32), nullable=True),
        sa.Column("referer", sa.Text, nullable=True),
        sa.Column("ip_addr", INET, nullable=True),
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column(
            "amount_cny",
            sa.Numeric(12, 2),
            nullable=True,
            comment="入金 / 交易额 (signup / deposit / first_trade 时填)",
        ),
        sa.Column(
            "attributed",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
            comment="CPS 分成核销标志; 财务对账后置 true",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # --- conversion_events 二级索引 (4 个 B-tree, 全部 created_at DESC) ---
    op.execute(
        """
        CREATE INDEX ix_conversion_events_broker_event_created
        ON conversion_events (broker_id, event_type, created_at DESC);
        """
    )
    op.execute(
        """
        CREATE INDEX ix_conversion_events_user_created
        ON conversion_events (user_id, created_at DESC);
        """
    )
    op.execute(
        """
        CREATE INDEX ix_conversion_events_utm_campaign_created
        ON conversion_events (utm_campaign, created_at DESC);
        """
    )
    op.create_index(
        "ix_conversion_events_attributed_created",
        "conversion_events",
        ["attributed", "created_at"],
    )


def downgrade() -> None:
    # 反向: conversion_events 先删 (依赖 brokers FK), brokers 后删.
    op.drop_table("conversion_events")
    op.drop_table("brokers")
