"""BE-S4-001 集成测试: ipos 历史字段 + 3 个索引 schema 验证.

覆盖矩阵 (5 条):
1. test_migration_0008_creates_columns_and_indexes
   schema_at_head 后 ipos 多 3 列 + 3 个新索引齐
2. test_first_day_change_pct_round_trip
   ``first_day_change_pct NUMERIC(8,4)`` 精度 0.01% 不丢: ±18.5 / -28.5 / 156.0
3. test_one_lot_winning_rate_clamped_range
   ``one_lot_winning_rate NUMERIC(8,6)`` round-trip + NULL 默认
   (业务校验 [0,1] 在 BE-S4-002 回填脚本层做, schema 不强制)
4. test_partial_index_status_listed_only
   ``ix_ipos_status_listing_date`` partial WHERE status='listed' 只对 listed 行有效
   (验 pg_indexes.indexdef 含 WHERE 子句)
5. test_alembic_downgrade_0008_then_upgrade_idempotent
   退到 0007_vip (3 列 + 3 索引清, vip / broker / articles 仍在) → upgrade head 恢复

不验:
- BE-S4-002 回填脚本写值范围校验 (越界丢弃) — 回填 PR 自测
- BE-S4-003 list_historical / peer-aggregate API — 后续 PR e2e
- AI 规律分析候选池采样 — BE-S4-004
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from alembic import command
from app.db.models import IPO

pytestmark = pytest.mark.db


# ─── helper ─────────────────────────────────────────────────────────


def _build_alembic_config(test_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)
    return cfg


_NEW_COLUMNS = {
    "first_day_change_pct",
    "one_lot_winning_rate",
    "oversubscribe_multiple",
}
_NEW_INDEXES = {
    "ix_ipos_first_day_change",
    "ix_ipos_industry_year",
    "ix_ipos_status_listing_date",
}


# ─── 1. schema 验证: 3 列 + 3 索引齐 ───────────────────────────────


async def test_migration_0008_creates_columns_and_indexes(
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """schema_at_head 后, ipos 多 3 列 + 3 个新索引齐."""
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='ipos' "
                "AND column_name = ANY(:cols)"
            ),
            {"cols": list(_NEW_COLUMNS)},
        )
        cols = {r[0] for r in rows}
        assert cols == _NEW_COLUMNS, f"ipos 新列缺失或多余: {cols ^ _NEW_COLUMNS}"

        rows = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname='public' AND tablename='ipos' "
                "AND indexname = ANY(:idx)"
            ),
            {"idx": list(_NEW_INDEXES)},
        )
        all_idx = {r[0] for r in rows}
        missing = _NEW_INDEXES - all_idx
        assert not missing, f"ipos 新索引缺失: {missing}"


# ─── 2. first_day_change_pct round-trip 精度 ────────────────────────


@pytest.mark.parametrize(
    ("idx", "value"),
    [
        (0, Decimal("18.5000")),  # 上市首日 +18.5%
        (1, Decimal("-28.5000")),  # 上市首日 -28.5% (跌)
        (2, Decimal("156.0000")),  # 上市首日 +156% (热门 IPO)
        (3, Decimal("0.0001")),  # 极小正涨幅, 验 (8,4) 精度 0.01%
    ],
)
async def test_first_day_change_pct_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
    idx: int,
    value: Decimal,
) -> None:
    """``first_day_change_pct NUMERIC(8,4)`` round-trip 精度不丢, ±正负 + 极小值通过.

    BE-S4-002 回填脚本写入这些精度时不能被 SQLAlchemy 强转截断.
    """
    # code 字段 String(16): 用 ``0100{idx}.HK`` 格式避免 parametrize 间冲突
    code = f"0010{idx}.HK"
    async with session_factory() as s:
        ipo = IPO(
            code=code,
            name="测试历史 IPO",
            market="HK",
            industry_l1="测试行业",
            listing_date=date(2024, 1, 15),
            status="listed",
            first_day_change_pct=value,
            data_source="backfill-test",
        )
        s.add(ipo)
        await s.commit()
        ipo_id = ipo.ipo_id

    async with session_factory() as s:
        row = await s.execute(
            text("SELECT first_day_change_pct FROM ipos WHERE ipo_id = :iid"),
            {"iid": ipo_id},
        )
        got = row.scalar_one()
        assert got == value, f"round-trip 不一致: 写 {value} 读到 {got}"


# ─── 3. one_lot_winning_rate / oversubscribe_multiple 默认 NULL ─────


async def test_one_lot_winning_rate_default_null_for_a_share(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """A 股不写 one_lot_winning_rate / oversubscribe_multiple, 默认 NULL.

    雷达图 / AI 规律分析读到 NULL 时端层兜底 ("数据不足"), 不抛.
    HK 行写值范围 [0,1] 由 BE-S4-002 业务层校验, schema 不强制.
    """
    async with session_factory() as s:
        ipo_a = IPO(
            code="000001.SZ",
            name="A 股测试",
            market="A",
            industry_l1="银行",
            listing_date=date(2024, 6, 15),
            status="listed",
            first_day_change_pct=Decimal("44.0000"),  # A 股新股开盘多次熔断
        )
        ipo_hk = IPO(
            code="00200.HK",
            name="HK 测试",
            market="HK",
            industry_l1="科技",
            listing_date=date(2024, 6, 15),
            status="listed",
            first_day_change_pct=Decimal("12.5000"),
            one_lot_winning_rate=Decimal("0.250000"),  # 25%
            oversubscribe_multiple=Decimal("285.60"),  # 超 285 倍
        )
        s.add_all([ipo_a, ipo_hk])
        await s.commit()
        a_id, hk_id = ipo_a.ipo_id, ipo_hk.ipo_id

    async with session_factory() as s:
        row = await s.execute(
            text(
                "SELECT one_lot_winning_rate, oversubscribe_multiple "
                "FROM ipos WHERE ipo_id = :iid"
            ),
            {"iid": a_id},
        )
        wr, om = row.one()
        assert wr is None, "A 股 one_lot_winning_rate 应 NULL"
        assert om is None, "A 股 oversubscribe_multiple 应 NULL"

        row = await s.execute(
            text(
                "SELECT one_lot_winning_rate, oversubscribe_multiple "
                "FROM ipos WHERE ipo_id = :iid"
            ),
            {"iid": hk_id},
        )
        wr, om = row.one()
        assert wr == Decimal("0.250000")
        assert om == Decimal("285.60")


# ─── 4. partial 索引: WHERE status='listed' ────────────────────────


async def test_partial_index_status_listed_only(
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """``ix_ipos_status_listing_date`` partial 索引 indexdef 含 ``WHERE status='listed'``.

    设计要点: 历史 IPO 检索只关心 listed 状态, partial 索引体积小 + 命中率高;
    upcoming/subscribing/withdrawn 不进索引, 防污染.
    """
    async with db_engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname='public' AND tablename='ipos' "
                "AND indexname='ix_ipos_status_listing_date'"
            ),
        )
        indexdef = row.scalar_one()
        assert "WHERE" in indexdef.upper(), (
            "ix_ipos_status_listing_date 应是 partial 索引"
        )
        assert "'listed'" in indexdef.lower() or "listed" in indexdef.lower(), (
            f"partial 谓词应含 status='listed', 实际: {indexdef}"
        )


# ─── 5. downgrade 0008 → 0007 → upgrade head 幂等 ──────────────────


async def test_alembic_downgrade_0008_then_upgrade_idempotent(
    test_database_url: str,
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """downgrade 到 0007_vip → 3 列 + 3 索引清, vip / broker / articles 仍在 → upgrade head 恢复."""
    cfg = _build_alembic_config(test_database_url)

    # 0. 起步: 3 列 + 3 索引都在
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='ipos' "
                "AND column_name = ANY(:cols)"
            ),
            {"cols": list(_NEW_COLUMNS)},
        )
        assert {r[0] for r in rows} == _NEW_COLUMNS

    # 1. downgrade 到 0007_vip (3 列 + 3 索引清)
    await asyncio.to_thread(command.downgrade, cfg, "0007_vip")
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='ipos' "
                "AND column_name = ANY(:cols)"
            ),
            {"cols": list(_NEW_COLUMNS)},
        )
        assert {r[0] for r in rows} == set(), "downgrade 后 3 列应消失"

        rows = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname='public' AND tablename='ipos' "
                "AND indexname = ANY(:idx)"
            ),
            {"idx": list(_NEW_INDEXES)},
        )
        assert {r[0] for r in rows} == set(), "downgrade 后 3 索引应消失"

        # vip / broker / articles 仍在 (上一版本)
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename IN ('vip_orders','vip_memberships',"
                "'brokers','conversion_events','articles','ipos')"
            )
        )
        kept = {r[0] for r in rows}
        assert kept == {
            "vip_orders",
            "vip_memberships",
            "brokers",
            "conversion_events",
            "articles",
            "ipos",
        }

    # 2. upgrade 回 head (兜底: try/finally 保证 schema 恢复)
    try:
        await asyncio.to_thread(command.upgrade, cfg, "head")
        async with db_engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='ipos' "
                    "AND column_name = ANY(:cols)"
                ),
                {"cols": list(_NEW_COLUMNS)},
            )
            assert {r[0] for r in rows} == _NEW_COLUMNS
    except Exception:
        await asyncio.to_thread(command.upgrade, cfg, "head")
        raise


# 提示: BE-S4-002 历史回填脚本会大量写入这些字段, 真业务越界校验 (例如
# first_day_change_pct ∈ [-100, 5000]) 在脚本层做; schema 不强制约束是为了让
# 极端值 (如某 HK 妖股 +600%) 不被误丢.

_ = (datetime, UTC)  # 防 ruff 误删未用 import; ipo 模型 timestamp 列可能用到
