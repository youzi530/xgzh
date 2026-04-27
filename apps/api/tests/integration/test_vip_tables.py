"""BE-S3-009 集成测试: vip_memberships + vip_orders schema / 约束 / 级联.

覆盖矩阵 (10 条):
1. test_migration_creates_vip_tables_with_indexes
   schema_at_head 后 2 张表 + 5 个二级索引 + 2 个 UNIQUE 齐
2. test_vip_orders_unique_out_trade_no
   UNIQUE(out_trade_no) 生效: 同商户单号不能重复入库 (BE-S3-010 回调幂等键)
3. test_vip_orders_default_status_pending
   ``server_default 'pending'``: 不传 status 时 PG 填 'pending'
4. test_vip_memberships_unique_user_id
   UNIQUE(user_id) 生效: 1 用户只能有 1 行 vip_memberships (一对一)
5. test_vip_memberships_user_cascade_delete
   删 user → 关联 vip_memberships CASCADE 清 (注销 = 删订阅)
6. test_vip_orders_user_cascade_delete
   删 user → 关联 vip_orders CASCADE 清 (注销 = 删订单, 私密支付不留历史)
7. test_vip_memberships_current_order_set_null_on_delete
   删 vip_orders → vip_memberships.current_order_id SET NULL (软关联, 不破主表)
8. test_vip_memberships_lifetime_end_at_far_future
   lifetime end_at = 9999-12-31 round-trip (避免 NULL 分支)
9. test_vip_orders_jsonb_raw_callback_round_trip
   JSONB raw_callback 验签后完整 payload round-trip + Numeric amount_cny 精度
10. test_alembic_downgrade_0007_then_upgrade_idempotent
    退到 0006_brokers (vip 表清, broker 仍在) → upgrade head 幂等

不验:
- BE-S3-009 grant_trial 状态机 / 续费堆叠 (业务 service 层 e2e)
- BE-S3-009 expire_overdue scheduler job (业务 e2e + cron mock)
- BE-S3-010 微信支付下单 / 验签 / 回调流转 (后续 PR)
- ``_resolve_plan`` 接真表 (后续 PR)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from alembic import command
from app.db.models import User, VipMembership, VipOrder

pytestmark = pytest.mark.db


# ─── helper ─────────────────────────────────────────────────────────


def _build_alembic_config(test_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)
    return cfg


_VIP_TABLES = {"vip_orders", "vip_memberships"}
_VIP_INDEXES = {
    "uq_vip_orders_out_trade_no",
    "ix_vip_orders_user_created",
    "ix_vip_orders_status_created",
    "ix_vip_orders_payment_channel_created",
    "uq_vip_memberships_user_id",
    "ix_vip_memberships_status_end_at",
    "ix_vip_memberships_end_at",
}


async def _make_user(session: AsyncSession, suffix: str) -> User:
    """构造一个 User; suffix 控制 phone / invite_code 唯一."""
    u = User(phone=f"+86138000{suffix}", invite_code=f"VIP{suffix}")
    session.add(u)
    await session.flush()
    return u


# ─── 1. schema 验证 ───────────────────────────────────────────────


async def test_migration_creates_vip_tables_with_indexes(
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """schema_at_head 后, vip_orders + vip_memberships + 5 个索引 / 2 个 UNIQUE 齐."""
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename = ANY(:ts)"
            ),
            {"ts": list(_VIP_TABLES)},
        )
        tables = {r[0] for r in rows}
        assert tables == _VIP_TABLES, (
            f"vip 表缺失或多余: {tables ^ _VIP_TABLES}"
        )

        rows = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname='public' AND tablename = ANY(:ts)"
            ),
            {"ts": list(_VIP_TABLES)},
        )
        all_idx = {r[0] for r in rows}
        missing = _VIP_INDEXES - all_idx
        assert not missing, f"二级索引/UNIQUE 缺失: {missing}"


# ─── 2. UNIQUE(out_trade_no) ───────────────────────────────────────


async def test_vip_orders_unique_out_trade_no(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """同 ``out_trade_no`` 二次插入抛 IntegrityError (BE-S3-010 微信支付回调幂等键)."""
    async with session_factory() as s:
        u = await _make_user(s, "0010001")
        order = VipOrder(
            user_id=u.user_id,
            out_trade_no="XGZH-20260427-0001",
            plan="monthly",
            amount_cny=Decimal("39.00"),
            payment_channel="wechat_mp",
        )
        s.add(order)
        await s.commit()

    with pytest.raises(IntegrityError):
        async with session_factory() as s:
            u2 = await _make_user(s, "0010002")
            order2 = VipOrder(
                user_id=u2.user_id,
                out_trade_no="XGZH-20260427-0001",  # 同单号
                plan="quarterly",
                amount_cny=Decimal("99.00"),
                payment_channel="wechat_mp",
            )
            s.add(order2)
            await s.commit()


# ─── 3. vip_orders.status server_default 'pending' ────────────────


async def test_vip_orders_default_status_pending(
    session_factory: async_sessionmaker[AsyncSession],
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """不显式传 ``status`` 时, server_default 'pending' 兜底.

    BE-S3-010 下单 service 层显式传 'pending', 但 schema 层兜底防漏写.
    """
    async with session_factory() as s:
        u = await _make_user(s, "0010003")
        # 直接 raw SQL 避开 ORM 默认值, 验 PG server_default
        result = await s.execute(
            text(
                "INSERT INTO vip_orders "
                "(user_id, out_trade_no, plan, amount_cny, payment_channel) "
                "VALUES (:uid, :otn, 'monthly', 39.00, 'wechat_mp') "
                "RETURNING order_id, status"
            ),
            {
                "uid": str(u.user_id),
                "otn": "XGZH-DEFAULT-001",
            },
        )
        order_id, status = result.one()
        await s.commit()

    async with db_engine.connect() as conn:
        row = await conn.execute(
            text("SELECT status FROM vip_orders WHERE order_id = :oid"),
            {"oid": order_id},
        )
        assert row.scalar_one() == "pending"
    assert status == "pending"


# ─── 4. UNIQUE(user_id) 一对一 ─────────────────────────────────────


async def test_vip_memberships_unique_user_id(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """同 ``user_id`` 不能挂 2 行 vip_memberships (一对一; 续费走覆盖 / 堆叠).

    业务读永远 ``WHERE user_id = ?`` LIMIT 1 不带 ORDER BY, 物理保证 1 行.
    """
    now = datetime.now(UTC)
    async with session_factory() as s:
        u = await _make_user(s, "0020001")
        m1 = VipMembership(
            user_id=u.user_id,
            status="trialing",
            plan="trial",
            start_at=now,
            end_at=now + timedelta(days=7),
        )
        s.add(m1)
        await s.commit()
        user_id = u.user_id

    with pytest.raises(IntegrityError):
        async with session_factory() as s:
            m2 = VipMembership(
                user_id=user_id,  # 同 user_id
                status="active",
                plan="monthly",
                start_at=now,
                end_at=now + timedelta(days=30),
            )
            s.add(m2)
            await s.commit()


# ─── 5. CASCADE: 删 user → vip_memberships 清 ─────────────────────


async def test_vip_memberships_user_cascade_delete(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """物理删 user → vip_memberships 整行 CASCADE 清.

    与 conversion_events.user_id (SET NULL) 不同思路: 订阅是用户独占数据,
    注销 = 彻底删. 与 user_favorites 一致.
    """
    now = datetime.now(UTC)
    async with session_factory() as s:
        u = await _make_user(s, "0030001")
        m = VipMembership(
            user_id=u.user_id,
            status="active",
            plan="monthly",
            start_at=now,
            end_at=now + timedelta(days=30),
        )
        s.add(m)
        await s.commit()
        user_id = u.user_id

    async with session_factory() as s:
        await s.execute(
            text("DELETE FROM users WHERE user_id = :uid"),
            {"uid": user_id},
        )
        await s.commit()

    async with session_factory() as s:
        row = await s.execute(
            text("SELECT count(*) FROM vip_memberships WHERE user_id = :uid"),
            {"uid": user_id},
        )
        assert row.scalar_one() == 0, "删 user → vip_memberships 应 CASCADE 清"


# ─── 6. CASCADE: 删 user → vip_orders 清 ─────────────────────────


async def test_vip_orders_user_cascade_delete(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """物理删 user → 该用户所有 vip_orders 整行 CASCADE 清.

    支付订单是私密数据, 用户注销后应彻底清 (与 conversion_events 不同思路).
    """
    async with session_factory() as s:
        u = await _make_user(s, "0030002")
        s.add_all(
            [
                VipOrder(
                    user_id=u.user_id,
                    out_trade_no=f"XGZH-CASC-{i:04d}",
                    plan="monthly",
                    amount_cny=Decimal("39.00"),
                    payment_channel="wechat_mp",
                )
                for i in range(3)
            ]
        )
        await s.commit()
        user_id = u.user_id

    async with session_factory() as s:
        await s.execute(
            text("DELETE FROM users WHERE user_id = :uid"),
            {"uid": user_id},
        )
        await s.commit()

    async with session_factory() as s:
        row = await s.execute(
            text("SELECT count(*) FROM vip_orders WHERE user_id = :uid"),
            {"uid": user_id},
        )
        assert row.scalar_one() == 0


# ─── 7. SET NULL: 删 vip_orders → vip_memberships.current_order_id ─


async def test_vip_memberships_current_order_set_null_on_delete(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """物理删 vip_orders → vip_memberships.current_order_id SET NULL, 主表不破.

    主链路依然通过 vip_orders ``WHERE user_id = ? ORDER BY created_at DESC``
    倒推订单历史; current_order_id 仅是性能优化指针.
    """
    now = datetime.now(UTC)
    async with session_factory() as s:
        u = await _make_user(s, "0040001")
        order = VipOrder(
            user_id=u.user_id,
            out_trade_no="XGZH-SETNULL-001",
            plan="monthly",
            amount_cny=Decimal("39.00"),
            status="paid",
            payment_channel="wechat_mp",
            paid_at=now,
        )
        s.add(order)
        await s.flush()
        membership = VipMembership(
            user_id=u.user_id,
            status="active",
            plan="monthly",
            start_at=now,
            end_at=now + timedelta(days=30),
            current_order_id=order.order_id,
            total_paid_cny=Decimal("39.00"),
        )
        s.add(membership)
        await s.commit()
        user_id = u.user_id
        order_id = order.order_id

    async with session_factory() as s:
        await s.execute(
            text("DELETE FROM vip_orders WHERE order_id = :oid"),
            {"oid": order_id},
        )
        await s.commit()

    async with session_factory() as s:
        row = await s.execute(
            text(
                "SELECT current_order_id FROM vip_memberships "
                "WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        assert row.scalar_one() is None, (
            "删 vip_orders 后 current_order_id 应 SET NULL, 不应 CASCADE 删主表"
        )

        # 主表仍在
        row = await s.execute(
            text(
                "SELECT count(*) FROM vip_memberships WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        assert row.scalar_one() == 1


# ─── 8. lifetime end_at = 9999-12-31 round-trip ────────────────────


async def test_vip_memberships_lifetime_end_at_far_future(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """lifetime 订阅 end_at = 9999-12-31 round-trip (避免 NULL 分支).

    业务侧 ``_resolve_plan`` 走 ``end_at > now()`` 单条件分支判断, 不需要
    ``IS NULL OR end_at > now()`` 的 OR 复杂查询.
    """
    far_future = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)
    now = datetime.now(UTC)
    async with session_factory() as s:
        u = await _make_user(s, "0050001")
        m = VipMembership(
            user_id=u.user_id,
            status="active",
            plan="lifetime",
            start_at=now,
            end_at=far_future,
            total_paid_cny=Decimal("999.00"),
        )
        s.add(m)
        await s.commit()
        user_id = u.user_id

    async with session_factory() as s:
        row = await s.execute(
            text(
                "SELECT end_at, plan FROM vip_memberships WHERE user_id = :uid"
            ),
            {"uid": user_id},
        )
        end_at, plan = row.one()
        assert plan == "lifetime"
        assert end_at.year == 9999
        # 业务读模拟: WHERE end_at > now() 应命中
        row = await s.execute(
            text(
                "SELECT count(*) FROM vip_memberships "
                "WHERE user_id = :uid AND end_at > now()"
            ),
            {"uid": user_id},
        )
        assert row.scalar_one() == 1, (
            "lifetime end_at=9999 应被 ``end_at > now()`` 捕获"
        )


# ─── 9. JSONB raw_callback round-trip ───────────────────────────────


async def test_vip_orders_jsonb_raw_callback_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """``raw_callback`` JSONB + ``amount_cny`` Numeric round-trip.

    BE-S3-010 微信回调验签后存原始 payload, 审计 / 排错用; 不能丢精度.
    """
    callback_payload = {
        "id": "EV-2018-02-28-13:00:00",
        "create_time": "2026-04-27T15:08:00+08:00",
        "resource_type": "encrypt-resource",
        "event_type": "TRANSACTION.SUCCESS",
        "summary": "支付成功",
        "resource": {
            "original_type": "transaction",
            "algorithm": "AEAD_AES_256_GCM",
            "decrypted_data": {
                "transaction_id": "4200001234567890",
                "out_trade_no": "XGZH-20260427-9999",
                "trade_state": "SUCCESS",
                "amount": {"total": 3900, "currency": "CNY"},
            },
        },
    }
    async with session_factory() as s:
        u = await _make_user(s, "0060001")
        order = VipOrder(
            user_id=u.user_id,
            out_trade_no="XGZH-JSONB-001",
            plan="monthly",
            amount_cny=Decimal("39.00"),
            status="paid",
            payment_channel="wechat_mp",
            transaction_id="4200001234567890",
            paid_at=datetime.now(UTC),
            raw_callback=callback_payload,
        )
        s.add(order)
        await s.commit()
        order_id = order.order_id

    async with db_engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT raw_callback, amount_cny, transaction_id "
                "FROM vip_orders WHERE order_id = :oid"
            ),
            {"oid": order_id},
        )
        cb, amt, tid = row.one()
        assert cb == callback_payload
        assert amt == Decimal("39.00")
        assert tid == "4200001234567890"

        # JSONB ->> 也能命中 (业务侧 BE-S3-010 验签后查 trade_state 用)
        row = await conn.execute(
            text(
                "SELECT count(*) FROM vip_orders "
                "WHERE raw_callback #>> "
                "'{resource,decrypted_data,trade_state}' = 'SUCCESS'"
            ),
        )
        assert row.scalar_one() == 1


# ─── 10. alembic downgrade 0007 → 0006 → upgrade head ──────────────


async def test_alembic_downgrade_0007_then_upgrade_idempotent(
    test_database_url: str,
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """``downgrade 0006_brokers`` 仅 drop vip 2 张表, broker / articles 仍在 → upgrade 恢复."""
    cfg = _build_alembic_config(test_database_url)

    # 0. 起步: vip 2 张表 + broker 2 张表都在
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename IN ('vip_orders','vip_memberships',"
                "'brokers','conversion_events')"
            )
        )
        assert {r[0] for r in rows} == {
            "vip_orders",
            "vip_memberships",
            "brokers",
            "conversion_events",
        }

    # 1. downgrade 到 0006_brokers (vip 表清, broker 仍在)
    await asyncio.to_thread(command.downgrade, cfg, "0006_brokers")
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename IN ('vip_orders','vip_memberships')"
            )
        )
        assert {r[0] for r in rows} == set()
        # broker 应仍在 (上一版本)
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename IN ('brokers','conversion_events')"
            )
        )
        assert {r[0] for r in rows} == {"brokers", "conversion_events"}

    # 2. upgrade 回 head (兜底: try/finally 保证 schema 恢复)
    try:
        await asyncio.to_thread(command.upgrade, cfg, "head")
        async with db_engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename IN ('vip_orders','vip_memberships')"
                )
            )
            assert {r[0] for r in rows} == _VIP_TABLES
    except Exception:
        await asyncio.to_thread(command.upgrade, cfg, "head")
        raise


# 提示: grant_trial / 续费堆叠 / scheduler expire job / _resolve_plan 接真表
# 等业务行为留到 BE-S3-009 service 层 PR e2e 覆盖, 本 PR 仅锁 schema 形状.
