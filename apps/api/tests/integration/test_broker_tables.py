"""BE-S3-007 / 008 集成测试: brokers + conversion_events schema / 约束 / 级联.

覆盖矩阵 (10 条):
1. test_migration_creates_broker_tables_with_indexes
   schema_at_head 后 2 张表 + 6 个二级索引 + 2 个 UNIQUE 齐
2. test_brokers_unique_slug
   UNIQUE(slug) 生效: 同 slug 二次插入抛 IntegrityError
3. test_brokers_jsonb_round_trip
   market_support / fees / features / promotion JSONB 写读对称, 不丢精度
4. test_brokers_default_partnership_type_none
   ``server_default 'NONE'``: 不传 partnership_type 时 PG 填 'NONE'
5. test_brokers_soft_delete_via_deleted_at
   SoftDeleteMixin: deleted_at 默认 NULL, 业务读用 ``deleted_at IS NULL`` 过滤
6. test_conversion_events_default_attributed_and_utm_source
   ``attributed`` / ``utm_source`` server_default 验证 (false / 'xgzh')
7. test_conversion_events_broker_cascade_delete
   删 broker → 关联 conversion_events 整行 CASCADE 清
8. test_conversion_events_user_set_null_on_delete
   删 user → conversion_events.user_id SET NULL, 历史不丢
9. test_conversion_events_inet_jsonb_types
   INET 类型 round-trip + amount_cny Numeric 精度
10. test_alembic_downgrade_0006_then_upgrade_idempotent
    退到 0005_articles → 升回 head 幂等; 退期间 vip 表也连带清, articles 应仍在

不验:
- BE-S3-007 横向对比 API + seeds 落地 (后续业务侧 PR)
- BE-S3-008 redirect 端点 + Redis 防刷 (后续业务侧 PR)
- BE-S3-008 ``stats_30d`` 聚合查询 (后续 e2e)
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from alembic import command
from app.db.models import Broker, ConversionEvent, User

pytestmark = pytest.mark.db


# ─── helper ─────────────────────────────────────────────────────────


def _build_alembic_config(test_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)
    return cfg


_BROKER_TABLES = {"brokers", "conversion_events"}
_BROKER_INDEXES = {
    "uq_brokers_slug",
    "ix_brokers_is_active_display_order",
    "ix_conversion_events_broker_event_created",
    "ix_conversion_events_user_created",
    "ix_conversion_events_utm_campaign_created",
    "ix_conversion_events_attributed_created",
}


def _make_broker(
    *,
    name_zh: str = "富途牛牛",
    slug: str = "futubull",
    market_support: list[str] | None = None,
    fees: dict | None = None,
    features: dict | None = None,
    promotion: dict | None = None,
) -> Broker:
    """构造一个 sane 默认券商, 用例只覆盖关心的字段."""
    return Broker(
        name_zh=name_zh,
        name_en=None,
        slug=slug,
        market_support=market_support if market_support is not None else ["HK", "US"],
        licenses=["SFC-1"],
        fees=fees if fees is not None else {"hk_commission_rate": 0.0003},
        features=features if features is not None else {"ipo_subscription": True},
        promotion=promotion if promotion is not None else {"is_active": False},
    )


# ─── 1. schema 验证 ───────────────────────────────────────────────


async def test_migration_creates_broker_tables_with_indexes(
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """schema_at_head 后, brokers + conversion_events + 6 个索引 / 2 个 UNIQUE 齐."""
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename = ANY(:ts)"
            ),
            {"ts": list(_BROKER_TABLES)},
        )
        tables = {r[0] for r in rows}
        assert tables == _BROKER_TABLES, (
            f"broker 表缺失或多余: {tables ^ _BROKER_TABLES}"
        )

        rows = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname='public' AND tablename = ANY(:ts)"
            ),
            {"ts": list(_BROKER_TABLES)},
        )
        all_idx = {r[0] for r in rows}
        missing = _BROKER_INDEXES - all_idx
        assert not missing, f"二级索引/UNIQUE 缺失: {missing}"


# ─── 2. UNIQUE(slug) 约束 ───────────────────────────────────────────


async def test_brokers_unique_slug(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """同 ``slug`` 二次插入抛 IntegrityError (FE 详情路由用 slug, 必须全局唯一)."""
    async with session_factory() as s:
        s.add(_make_broker(name_zh="富途", slug="futubull"))
        await s.commit()

    with pytest.raises(IntegrityError):
        async with session_factory() as s:
            s.add(_make_broker(name_zh="富途新版", slug="futubull"))
            await s.commit()


# ─── 3. JSONB round-trip ────────────────────────────────────────────


async def test_brokers_jsonb_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """7 个 JSONB 字段 (market_support / licenses / fees / features / promotion) 写读对称.

    BE-S3-007 业务侧 ``BrokerPublic.model_validate`` 完全依赖此对称性.
    """
    fees = {
        "hk_commission_rate": 0.0003,
        "hk_min_commission": 3.0,
        "a_commission_rate": 0.00025,
        "platform_fee": 15.0,
        "margin_rate_hkd": 0.058,
        "cancel_fee": 0,
    }
    features = {
        "ipo_subscription": True,
        "dark_pool_trading": True,
        "margin_trading": True,
        "chinese_service": True,
        "min_deposit_hkd": 0,
    }
    promotion = {
        "is_active": True,
        "title": "新户入金礼 8888",
        "description": "新户首次入金 ≥10K HKD 即送 8888 HKD",
        "end_at": "2026-12-31T23:59:59+08:00",
        "invite_code": "XGZH2026",
        "referral_url": "https://www.futuhk.com/?utm_source=xgzh",
    }
    async with session_factory() as s:
        b = _make_broker(
            slug="futubull-rt",
            market_support=["HK", "A", "US", "SG"],
            fees=fees,
            features=features,
            promotion=promotion,
        )
        s.add(b)
        await s.commit()
        broker_id = b.broker_id

    async with session_factory() as s:
        row = await s.execute(
            text(
                "SELECT market_support, licenses, fees, features, promotion "
                "FROM brokers WHERE broker_id = :bid"
            ),
            {"bid": broker_id},
        )
        ms, lic, ff, ft, pm = row.one()
        assert ms == ["HK", "A", "US", "SG"]
        assert lic == ["SFC-1"]
        assert ff == fees
        assert ft == features
        assert pm == promotion


# ─── 4. partnership_type server_default 'NONE' ─────────────────────


async def test_brokers_default_partnership_type_none(
    session_factory: async_sessionmaker[AsyncSession],
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """不显式设 ``partnership_type`` 时, server_default 'NONE' 兜底.

    业务语义: 默认所有券商都是 'NONE' (无合作), 财务对账 cron 只看
    ``partnership_type IN ('CPA','CPS','BOTH')`` 的行 — 加新券商不需要先填合作字段.
    """
    async with session_factory() as s:
        b = _make_broker(slug="futubull-default")
        s.add(b)
        await s.commit()
        broker_id = b.broker_id

    async with db_engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT partnership_type, partnership_cpa_amount, "
                "partnership_cps_rate, display_order, is_active "
                "FROM brokers WHERE broker_id = :bid"
            ),
            {"bid": broker_id},
        )
        pt, cpa, cps, do, ia = row.one()
        assert pt == "NONE"
        assert cpa is None
        assert cps is None
        assert do == 0
        assert ia is True


# ─── 5. SoftDelete: deleted_at ──────────────────────────────────────


async def test_brokers_soft_delete_via_deleted_at(
    session_factory: async_sessionmaker[AsyncSession],
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """SoftDeleteMixin: ``deleted_at`` 默认 NULL, 设值后业务读应用 ``deleted_at IS NULL`` 滤.

    与物理 DELETE 区分: 软删保留历史, conversion_events 仍能反查到该券商
    (BE-S3-008 财务对账场景).
    """
    async with session_factory() as s:
        b = _make_broker(slug="futubull-soft")
        s.add(b)
        await s.commit()
        broker_id = b.broker_id

    # 默认 deleted_at IS NULL
    async with db_engine.connect() as conn:
        row = await conn.execute(
            text("SELECT deleted_at FROM brokers WHERE broker_id = :bid"),
            {"bid": broker_id},
        )
        assert row.scalar_one() is None

    # 软删 (业务侧 service 层一行 UPDATE)
    async with session_factory() as s:
        await s.execute(
            text("UPDATE brokers SET deleted_at = now() WHERE broker_id = :bid"),
            {"bid": broker_id},
        )
        await s.commit()

    async with db_engine.connect() as conn:
        # 业务列表查询模拟 (deleted_at IS NULL 过滤)
        row = await conn.execute(
            text(
                "SELECT count(*) FROM brokers "
                "WHERE broker_id = :bid AND deleted_at IS NULL"
            ),
            {"bid": broker_id},
        )
        assert row.scalar_one() == 0, "软删后业务读应过滤掉该 broker"

        # 物理仍在
        row = await conn.execute(
            text("SELECT count(*) FROM brokers WHERE broker_id = :bid"),
            {"bid": broker_id},
        )
        assert row.scalar_one() == 1, "软删不应物理删除"


# ─── 6. conversion_events server_default ───────────────────────────


async def test_conversion_events_default_attributed_and_utm_source(
    session_factory: async_sessionmaker[AsyncSession],
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """``attributed`` 默认 false, ``utm_source`` 默认 'xgzh' (BE-S3-008 默认入口标识)."""
    async with session_factory() as s:
        b = _make_broker(slug="futubull-ce")
        s.add(b)
        await s.flush()
        ev = ConversionEvent(
            device_id="dev-anon-001",
            broker_id=b.broker_id,
            event_type="click",
        )
        s.add(ev)
        await s.commit()
        event_id = ev.event_id

    async with db_engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT attributed, utm_source FROM conversion_events "
                "WHERE event_id = :eid"
            ),
            {"eid": event_id},
        )
        attributed, utm_source = row.one()
        assert attributed is False
        assert utm_source == "xgzh"


# ─── 7. CASCADE: 删 broker → conversion_events 行清 ─────────────────


async def test_conversion_events_broker_cascade_delete(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """物理删 broker → 关联 conversion_events 整行 CASCADE 清.

    生产应走 SoftDelete (deleted_at), 物理删极少见; 但 schema 必须保证物理删时
    没有孤儿埋点.
    """
    async with session_factory() as s:
        b = _make_broker(slug="futubull-cascade")
        s.add(b)
        await s.flush()
        broker_id = b.broker_id
        s.add_all(
            [
                ConversionEvent(
                    device_id=f"dev-{i}",
                    broker_id=broker_id,
                    event_type="click",
                )
                for i in range(3)
            ]
        )
        await s.commit()

    async with session_factory() as s:
        await s.execute(
            text("DELETE FROM brokers WHERE broker_id = :bid"),
            {"bid": broker_id},
        )
        await s.commit()

    async with session_factory() as s:
        row = await s.execute(
            text(
                "SELECT count(*) FROM conversion_events WHERE broker_id = :bid"
            ),
            {"bid": broker_id},
        )
        assert row.scalar_one() == 0, "删 broker → conversion_events 应 CASCADE 清"


# ─── 8. SET NULL: 删 user → conversion_events.user_id NULL ─────────


async def test_conversion_events_user_set_null_on_delete(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """物理删 user → 该用户所有 conversion_events.user_id SET NULL, 行不删.

    与 invite_codes.owner_user_id / chat_sessions.user_id 同策略:
    用户注销不丢 CPA / CPS 财务历史, 仅匿名化.
    """
    async with session_factory() as s:
        u = User(phone="+8613800000007", invite_code="TESTBRK1")
        b = _make_broker(slug="futubull-setnull")
        s.add_all([u, b])
        await s.flush()
        user_id = u.user_id
        broker_id = b.broker_id
        ev = ConversionEvent(
            user_id=user_id,
            device_id="dev-007",
            broker_id=broker_id,
            event_type="signup",
            amount_cny=Decimal("0"),
        )
        s.add(ev)
        await s.commit()
        event_id = ev.event_id

    async with session_factory() as s:
        await s.execute(
            text("DELETE FROM users WHERE user_id = :uid"),
            {"uid": user_id},
        )
        await s.commit()

    async with session_factory() as s:
        row = await s.execute(
            text(
                "SELECT user_id, broker_id FROM conversion_events "
                "WHERE event_id = :eid"
            ),
            {"eid": event_id},
        )
        ev_user_id, ev_broker_id = row.one()
        assert ev_user_id is None, "用户注销 → user_id 应 SET NULL"
        assert ev_broker_id == broker_id, "broker_id 应不动"


# ─── 9. INET / Numeric 类型 round-trip ──────────────────────────────


async def test_conversion_events_inet_and_numeric_types(
    session_factory: async_sessionmaker[AsyncSession],
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """INET 列 (``ip_addr``) 和 Numeric (``amount_cny``) round-trip 不丢精度.

    INET 比 ``String(45)`` 强: PG 校验非法 IP, 支持 subnet 查询 (后续防刷扩展).
    """
    async with session_factory() as s:
        b = _make_broker(slug="futubull-inet")
        s.add(b)
        await s.flush()
        ev = ConversionEvent(
            device_id="dev-inet",
            broker_id=b.broker_id,
            event_type="deposit",
            ip_addr="203.0.113.42",
            amount_cny=Decimal("12345.67"),
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15"
            ),
        )
        s.add(ev)
        await s.commit()
        event_id = ev.event_id

    async with db_engine.connect() as conn:
        # 用 ``host(ip_addr)`` 而非 ``::text``: PG INET 把单 IP 也当 CIDR 存, ::text
        # 会带 /32 掩码; ``host()`` 函数脱掉掩码返纯 IP 字符串.
        row = await conn.execute(
            text(
                "SELECT host(ip_addr), amount_cny FROM conversion_events "
                "WHERE event_id = :eid"
            ),
            {"eid": event_id},
        )
        ip_addr_text, amt = row.one()
        assert ip_addr_text == "203.0.113.42"
        assert amt == Decimal("12345.67")

    # 非法 IP 应抛 (PG INET 校验): asyncpg 抛 InvalidTextRepresentationError,
    # SQLAlchemy 包成 DBAPIError → 父类是 SQLAlchemyError, 用它兜底比 Exception 严谨.
    from sqlalchemy.exc import SQLAlchemyError

    with pytest.raises(SQLAlchemyError):
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO conversion_events "
                    "(device_id, broker_id, event_type, ip_addr) "
                    "VALUES ('dev-bad', :bid, 'click', :ip)"
                ),
                # 用合法 broker 但非法 IP, 错误源就是 ip_addr (排除其它字段干扰)
                {"bid": b.broker_id, "ip": "999.999.999.999"},
            )


# ─── 10. alembic downgrade 0006 → 0005 → upgrade head 幂等 ────────


async def test_alembic_downgrade_0006_then_upgrade_idempotent(
    test_database_url: str,
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """``alembic downgrade 0005_articles`` drop broker + vip 4 张表 → ``upgrade head`` 恢复.

    本测试结尾必须 schema 回 head, 不然同 module 后续用例就崩.
    """
    cfg = _build_alembic_config(test_database_url)

    # 0. 起步: 4 张新表 (broker / vip) 都在
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename IN ('brokers','conversion_events',"
                "'vip_orders','vip_memberships')"
            )
        )
        assert {r[0] for r in rows} == {
            "brokers",
            "conversion_events",
            "vip_orders",
            "vip_memberships",
        }

    # 1. downgrade 到 0005_articles (broker + vip 表全清, articles + 0001-0004 仍在)
    await asyncio.to_thread(command.downgrade, cfg, "0005_articles")
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename IN ('brokers','conversion_events',"
                "'vip_orders','vip_memberships')"
            )
        )
        assert {r[0] for r in rows} == set(), (
            "downgrade 后 broker + vip 表必须 0 个; 残留意味 downgrade() 写漏"
        )
        # articles 应仍在 (上一版本)
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        )
        residual = {r[0] for r in rows}
        assert "articles" in residual and "users" in residual

    # 2. upgrade head 恢复 (兜底: try/finally 保证 schema 回 head)
    try:
        await asyncio.to_thread(command.upgrade, cfg, "head")
        async with db_engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename IN ('brokers','conversion_events',"
                    "'vip_orders','vip_memberships')"
                )
            )
            assert {r[0] for r in rows} == {
                "brokers",
                "conversion_events",
                "vip_orders",
                "vip_memberships",
            }
    except Exception:
        await asyncio.to_thread(command.upgrade, cfg, "head")
        raise


# 提示: ``stats_30d`` GROUP BY 聚合查询 / Redis 防刷 / utm_campaign 路由
# 等业务行为留到 BE-S3-007 / 008 业务侧 PR e2e 覆盖, 本 PR 仅锁 schema 形状.
