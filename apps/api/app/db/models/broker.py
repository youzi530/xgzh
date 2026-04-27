"""Broker 域 ORM (BE-S3-007 / 008): 券商主表 + 跳转转化事件.

Broker 是 Sprint 3 变现侧 (CPA / CPS) 的核心: BE-S3-007 (横向对比 API) +
BE-S3-008 (redirect + UTM + ConversionEvent 落表) + FE-S3-003 (券商对比页) +
将来 Sprint 4+ 财务对账后台 全部读写这两张表.

总体设计
========
- 主键 ``broker_id`` / ``event_id`` UUID, ``gen_random_uuid()`` (复用 0001 已建
  的 pgcrypto 扩展; 与 Sprint 1/2/3-001 风格一致).
- ``brokers`` 业务字段重: 7 个 JSONB 列 (market_support / licenses / fees /
  features / promotion + 外加 partnership_* 三个标量) — JSONB 是有意为之:
    * 各券商 fees / features schema 不一 (例 HK 才有 hk_commission, A 股专门 a_commission_rate),
      规范化拆表得 N 张子表, 写入端复杂度激增, 收益小
    * 横向对比 API 多以 `model_dump()` 直出 + FE 渲染, JSONB 直接 jsonify 极合适
    * 后续运营调整某券商 fees 不需要 ALTER TABLE, 改 ``brokers.fees`` 一行即可
- ``brokers.slug`` UNIQUE: URL 友好 (``/brokers/futubull`` 比 UUID 路径强), 详情页
  路由用 slug 而非 UUID (与 Sprint 3 spec/10 §BE-S3-007 AC 对齐).
- ``partnership_*`` 三字段属内部数据 (BrokerInternal): API 层用
  ``BrokerPublic.model_dump(exclude={'partnership_type', 'partnership_cpa_amount',
  'partnership_cps_rate'})`` 隔离, ORM 都存.
- 软删除: ``brokers`` 走 ``SoftDeleteMixin`` (deleted_at), 与 Sprint 1 ``users``
  同方案 — 历史 ConversionEvent 仍可关联到已下架券商, 不丢运营数据.
- ``conversion_events`` append-only: 不带 ``updated_at`` (与 ``chat_messages``
  / ``chat_token_usage`` 同), 写入即历史; 唯一可改字段 ``attributed`` 单独路径
  (财务对账 cron 走 raw SQL UPDATE), ORM 不暴露.

外键级联策略
============
- ``conversion_events.user_id`` → ``users.user_id`` ``ON DELETE SET NULL``
  (与 ``invite_codes.owner_user_id`` / ``chat_sessions.user_id`` 同 — 用户注销
  后埋点不丢, CPA / CPS 财务对账仍可追溯)
- ``conversion_events.broker_id`` → ``brokers.broker_id`` ``ON DELETE CASCADE``
  (券商物理删 = 该券商埋点全清; 但生产环境应走 SoftDelete, 物理删极少见)

索引设计 (BE-S3-007 + BE-S3-008 共 6 个二级索引 + 2 个 UNIQUE)
=============================================================
- ``brokers(slug)`` UNIQUE                             — 详情页 URL 路由
- ``brokers(is_active, display_order DESC)``           — 列表 API 默认排序
- ``conversion_events(broker_id, event_type, created_at DESC)``  — 券商 30d stats
- ``conversion_events(user_id, created_at DESC)``      — 用户行为追踪
- ``conversion_events(utm_campaign, created_at DESC)`` — 活动归因
- ``conversion_events(attributed, created_at)``        — 待核销 CPS 列表
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
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import SoftDeleteMixin, TimestampMixin


class Broker(Base, TimestampMixin, SoftDeleteMixin):
    """券商主表 (FE-S3-003 横向对比 + 详情 + 跳转的源数据).

    业务读路径:
    - 列表 API: ``WHERE is_active=true AND deleted_at IS NULL ORDER BY
      display_order DESC, created_at DESC`` + 按 ``market_support`` / 价格筛选
    - 详情 API: ``WHERE slug = $1 AND deleted_at IS NULL`` (URL 友好)
    - 跳转端点 (BE-S3-008): 取 ``promotion.referral_url`` + 拼 utm 参数 → 302

    seeds: BE-S3-007 落 6-8 家种子数据到 ``apps/api/seeds/brokers.json``,
    通过 ``apps/api/scripts/seed_brokers.py`` 幂等 upsert (按 slug).
    """

    __tablename__ = "brokers"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_brokers_slug"),
        Index("ix_brokers_is_active_display_order", "is_active", "display_order"),
    )

    broker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    name_zh: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="中文名 (主显), 如 '富途牛牛'",
    )
    name_en: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="英文名, 如 'Futu' (港股 / 国际化用)",
    )
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    slug: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="URL slug (futubull / tigerbrokers / longbridge); UNIQUE; FE 路由用",
    )

    market_support: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="支持市场 ['HK', 'A', 'US', 'SG']",
    )
    licenses: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        comment="持牌列表 ['SFC-1', 'SFC-4', 'SEC']",
    )
    fees: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment=(
            "费率 { hk_commission_rate, hk_min_commission, a_commission_rate, "
            "platform_fee, margin_rate_hkd, cancel_fee }; 各券商 schema 不一, "
            "JSONB 直存"
        ),
    )
    features: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment=(
            "功能 { ipo_subscription, dark_pool_trading, margin_trading, "
            "chinese_service, min_deposit_hkd } 五元组"
        ),
    )
    promotion: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment=(
            "活动 { is_active, title, description, end_at (ISO string), "
            "invite_code, referral_url }; end_at 用 string 而非 timestamp 列, "
            "减少 schema 变化成本"
        ),
    )

    # ─── partnership 三字段 (BrokerInternal only, 不直接出 API) ─────────
    partnership_type: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        server_default=text("'NONE'"),
        comment="CPA / CPS / BOTH / NONE; 决定财务对账逻辑",
    )
    partnership_cpa_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
        comment="每注册 1 用户返佣 CNY (CPA / BOTH 时填)",
    )
    partnership_cps_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 5),
        nullable=True,
        comment="入金 / 交易额分成比例 0.00000-1.00000 (CPS / BOTH 时填)",
    )

    display_order: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("0"),
        comment="运营手动排序权重; 越大越靠前",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="false = 暂时下架 (软隐藏); 与 deleted_at 区分: 后者是逻辑删除",
    )


class ConversionEvent(Base):
    """券商跳转转化事件 (BE-S3-008 端点 ``GET /brokers/{slug}/redirect`` 落库).

    事件流水, append-only. 写入即历史, 不带 ``updated_at``; 唯一可改字段
    ``attributed`` (财务对账核销时手工 / 脚本批量 UPDATE).

    业务读路径:
    - 券商 30d stats (运营 / VIP): ``WHERE broker_id=? AND event_type=? AND
      created_at >= now() - 30d`` GROUP BY (走 ``ix_*_broker_event_created``)
    - 待核销 CPS 列表 (财务对账): ``WHERE attributed=false AND event_type IN
      ('signup','deposit','first_trade')`` (走 ``ix_*_attributed_created``)
    - 用户行为追踪 (运营): ``WHERE user_id=? ORDER BY created_at DESC``

    防刷: BE-S3-008 service 层用 Redis key + EXPIRE 1h 实现"同 (user_id /
    device_id, broker_id, utm_campaign) 1h 内仅落 1 行 click 事件"; schema 层
    不加 unique constraint (signup / deposit 等其它 event_type 不限频, 加约束
    会误杀).
    """

    __tablename__ = "conversion_events"
    __table_args__ = (
        Index(
            "ix_conversion_events_broker_event_created",
            "broker_id",
            "event_type",
            text("created_at DESC"),
        ),
        Index(
            "ix_conversion_events_user_created",
            "user_id",
            text("created_at DESC"),
        ),
        Index(
            "ix_conversion_events_utm_campaign_created",
            "utm_campaign",
            text("created_at DESC"),
        ),
        # 待核销 CPS 列表: attributed=false 行很少, 这条索引体积可控
        Index(
            "ix_conversion_events_attributed_created",
            "attributed",
            "created_at",
        ),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        comment="可空: 匿名跳转也落埋点; 用户注销后 SET NULL 不丢历史",
    )
    device_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="前端拦截器自动注入 (与 push_tokens.device_id 同语义)",
    )
    broker_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("brokers.broker_id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="click / signup / kyc_pass / deposit / first_trade",
    )
    utm_source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'xgzh'"),
        comment="默认 'xgzh'; 多入口时可标记 'xgzh-app' / 'xgzh-mp'",
    )
    utm_campaign: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="活动 ID (/redirect?utm_campaign=ipo-202604)",
    )
    utm_medium: Mapped[str | None] = mapped_column(String(32), nullable=True)
    referer: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_addr: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    amount_cny: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
        comment="入金 / 交易额 (signup / deposit / first_trade 时填)",
    )
    attributed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="CPS 分成核销标志; 财务对账后置 true",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
