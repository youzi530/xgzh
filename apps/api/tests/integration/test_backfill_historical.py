"""BE-S4-002 集成测试: 历史 IPO 回填脚本 (fixture / synthetic / 幂等 / 越界丢弃).

覆盖矩阵 (8 条):
1. test_load_fixture_ok
   读 ``seeds/historical_ipos_fixture.json`` ~40 行 + 全量校验过, 9 行业 / HK + A
2. test_load_fixture_dup_key_raises
   tmp 文件含 (code, market) 重复 → ValueError
3. test_load_fixture_invalid_value_raises
   tmp 文件含 first_day_change_pct=10000 (越界) → ValueError
4. test_synthetic_generates_target_rows
   ``--target-rows=600`` → fixture(40) + synthetic(560), 全部 data_source 标记齐, 无重复 (code, market)
5. test_synthetic_deterministic_with_seed
   同 seed 两次生成结果完全一致 (确定性回归保证)
6. test_run_fixture_writes_db
   端到端: ``run --source fixture`` 写 40 行进 ipos 表, data_source 标 fixture-curated
7. test_run_idempotent
   连跑两次 ``run --source fixture`` → 第二次 inserted=0 (幂等)
8. test_run_dry_run_no_db_write
   dry-run 模式不写库 (truncate_all 后跑 dry-run, 表仍空)

不验:
- akshare 真网络回填 (需外网, 留 manual smoke 与 prod cron)
- 周期 cron 调度 (本 PR 范围外, 一次性回填脚本)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from scripts.backfill_historical_ipos import (
    DEFAULT_FIXTURE_FILE,
    generate_synthetic,
    load_fixture,
    run,
)

pytestmark = pytest.mark.db


# ─── 1. fixture 加载 ok ─────────────────────────────────────────────


def test_load_fixture_ok() -> None:
    """default fixture 路径加载 + 校验过, ≥ 30 行 / ≥ 5 行业 / 双 market."""
    rows = load_fixture(DEFAULT_FIXTURE_FILE)
    assert len(rows) >= 30, f"fixture 应 ≥ 30 行, 实际 {len(rows)}"

    industries = {r["industry_l1"] for r in rows}
    assert len(industries) >= 5, f"行业多样性应 ≥ 5, 实际 {industries}"

    markets = {r["market"] for r in rows}
    assert markets == {"HK", "A"}, f"双 market 缺失: {markets}"

    # data_source 全部标 backfill-fixture-curated
    sources = {r["data_source"] for r in rows}
    assert sources == {"backfill-fixture-curated"}


# ─── 2. fixture 重复 key 抛 ──────────────────────────────────────────


def test_load_fixture_dup_key_raises(tmp_path: Path) -> None:
    """fixture 含 (code, market) 重复 → ValueError, 不写任何一行."""
    f = tmp_path / "dup.json"
    f.write_text(
        json.dumps(
            [
                {
                    "code": "00100.HK",
                    "name": "A",
                    "market": "HK",
                    "industry_l1": "互联网",
                    "listing_date": "2023-01-01",
                    "status": "listed",
                    "data_source": "backfill-fixture-curated",
                },
                {
                    "code": "00100.HK",  # 重复
                    "name": "B",
                    "market": "HK",
                    "industry_l1": "互联网",
                    "listing_date": "2023-02-01",
                    "status": "listed",
                    "data_source": "backfill-fixture-curated",
                },
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="重复"):
        load_fixture(f)


# ─── 3. fixture 越界值抛 ─────────────────────────────────────────────


def test_load_fixture_invalid_value_raises(tmp_path: Path) -> None:
    """first_day_change_pct=10000 越界 [-100, 5000] → ValueError."""
    f = tmp_path / "bad.json"
    f.write_text(
        json.dumps(
            [
                {
                    "code": "00200.HK",
                    "name": "X",
                    "market": "HK",
                    "industry_l1": "测试",
                    "listing_date": "2023-01-01",
                    "first_day_change_pct": 10000,
                    "status": "listed",
                    "data_source": "backfill-fixture-curated",
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"first_day_change_pct.*\[-100, 5000\]"):
        load_fixture(f)


# ─── 4. synthetic 命中 target_rows ───────────────────────────────────


def test_synthetic_generates_target_rows() -> None:
    """target=600 → fixture(40) + synthetic(560); 全部 data_source 标 synthetic-2026, 无重复 key."""
    fixture_rows = load_fixture(DEFAULT_FIXTURE_FILE)
    synth = generate_synthetic(
        target_total=600,
        fixture_rows=fixture_rows,
        year_from=2022,
        year_to=2025,
        seed=42,
    )
    assert len(synth) == 600 - len(fixture_rows), f"synth 数量错: {len(synth)}"

    # 全部 data_source 标 synthetic-2026
    assert {r["data_source"] for r in synth} == {"synthetic-2026"}

    # synthetic 内部无重复 (code, market)
    keys: list[tuple[str, str]] = [(r["code"], r["market"]) for r in synth]
    assert len(set(keys)) == len(keys), "synthetic 内部 (code, market) 应唯一"

    # synthetic 与 fixture 无碰撞
    fixture_keys = {(r["code"], r["market"]) for r in fixture_rows}
    overlap = set(keys) & fixture_keys
    assert not overlap, f"synthetic 与 fixture 撞 code: {overlap}"

    # 字段范围合理性 (抽 10 行)
    for r in synth[:10]:
        assert -30 <= float(r["first_day_change_pct"]) <= 500
        if r["market"] == "HK":
            assert 0 <= float(r["one_lot_winning_rate"]) <= 1
        else:
            assert r["one_lot_winning_rate"] is None


# ─── 5. synthetic 确定性 (相同 seed 完全可重复) ─────────────────────


def test_synthetic_deterministic_with_seed() -> None:
    """同 seed 两次生成结果完全一致, 用 fixture 同源.

    BE-S4-002 e2e / fe-s4 调试时, 必须能保证"今天跑和明天跑结果一致",
    否则 e2e 用例无法稳定 assert 行数 / 排序.
    """
    fixture_rows = load_fixture(DEFAULT_FIXTURE_FILE)
    a = generate_synthetic(
        target_total=200,
        fixture_rows=fixture_rows,
        year_from=2022,
        year_to=2025,
        seed=123,
    )
    b = generate_synthetic(
        target_total=200,
        fixture_rows=fixture_rows,
        year_from=2022,
        year_to=2025,
        seed=123,
    )
    assert len(a) == len(b)
    # 比较前 10 行 code + first_day_change_pct (代表性抽样)
    for ra, rb in zip(a[:10], b[:10], strict=True):
        assert ra["code"] == rb["code"]
        assert ra["first_day_change_pct"] == rb["first_day_change_pct"]


# ─── 6. run --source fixture 写库 ────────────────────────────────────


async def test_run_fixture_writes_db(
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """端到端: ``run --source fixture --target-rows=...`` 写 40 行进 ipos 表."""
    code = await run(
        source="fixture",
        fixture_file=DEFAULT_FIXTURE_FILE,
        target_rows=0,
        year_from=2022,
        year_to=2025,
        seed=42,
        limit=500,
        dry_run=False,
    )
    assert code == 0

    async with db_engine.connect() as conn:
        cnt = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM ipos "
                    "WHERE data_source = 'backfill-fixture-curated'"
                )
            )
        ).scalar_one()
        assert cnt >= 30, f"fixture 写入应 ≥ 30 行, 实际 {cnt}"

        # 抽查腾讯控股 first_day_change_pct
        row = (
            await conn.execute(
                text(
                    "SELECT first_day_change_pct, market, industry_l1 FROM ipos "
                    "WHERE code = '00700.HK'"
                )
            )
        ).first()
        assert row is not None, "00700.HK 应已写入"
        assert row[0] is not None  # first_day_change_pct 写入
        assert row[1] == "HK"
        assert row[2] == "互联网"


# ─── 7. 幂等: 二次跑 inserted=0 ──────────────────────────────────────


async def test_run_idempotent(
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """连跑两次 ``run --source fixture`` → 总行数不增长 (幂等).

    BE-S4-002 §幂等保证: 二次跑相同命令 inserted=0; 测试比较前后 count 相等.
    """
    # 第一次
    code = await run(
        source="fixture",
        fixture_file=DEFAULT_FIXTURE_FILE,
        target_rows=0,
        year_from=2022,
        year_to=2025,
        seed=42,
        limit=500,
        dry_run=False,
    )
    assert code == 0

    async with db_engine.connect() as conn:
        cnt_first = (
            await conn.execute(text("SELECT count(*) FROM ipos"))
        ).scalar_one()

    # 第二次
    code = await run(
        source="fixture",
        fixture_file=DEFAULT_FIXTURE_FILE,
        target_rows=0,
        year_from=2022,
        year_to=2025,
        seed=42,
        limit=500,
        dry_run=False,
    )
    assert code == 0

    async with db_engine.connect() as conn:
        cnt_second = (
            await conn.execute(text("SELECT count(*) FROM ipos"))
        ).scalar_one()

    assert cnt_first == cnt_second, (
        f"幂等性破坏: 第一次写入 {cnt_first} 行, 第二次后变成 {cnt_second}"
    )


# ─── 8. dry-run 不写库 ────────────────────────────────────────────────


async def test_run_dry_run_no_db_write(
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """dry-run 模式: 仅打 stats 不写库; 跑完 ipos 表仍空."""
    code = await run(
        source="synthetic",
        fixture_file=DEFAULT_FIXTURE_FILE,
        target_rows=600,
        year_from=2022,
        year_to=2025,
        seed=42,
        limit=500,
        dry_run=True,
    )
    assert code == 0

    async with db_engine.connect() as conn:
        cnt = (
            await conn.execute(text("SELECT count(*) FROM ipos"))
        ).scalar_one()
        assert cnt == 0, f"dry-run 不应写库, 实际 {cnt} 行"


_ = (Any,)  # 防 ruff 误删: 未来扩 mock akshare 测试时会用
