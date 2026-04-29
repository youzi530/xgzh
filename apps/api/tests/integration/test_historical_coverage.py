"""QA-S5-001 BC-1/2/7 集成测试: 历史 IPO coverage 检查脚本.

覆盖矩阵 (5 条):
1. test_coverage_empty_db
   空 DB → all 0 + passed=False (达不到任何 AC threshold)
2. test_coverage_after_synthetic_backfill
   跑 backfill synthetic 600 行 → industry_pct ≥ 80% + first_day_pct ≥ 60% (AC 满足)
3. test_coverage_buckets_grouped_by_data_source
   两类 data_source 行混存 → buckets 列表正确分桶 + 各桶 pct 独立计算
4. test_coverage_threshold_pass_fail
   动态 threshold: 100% 全 fixture 跑过, 但 ``--industry-min 110`` 故意失败 → returns 1
5. test_coverage_json_format
   ``--format json`` 输出结构合法 + 含 ``passed`` / ``buckets`` 字段
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from scripts.backfill_historical_ipos import DEFAULT_FIXTURE_FILE
from scripts.backfill_historical_ipos import run as backfill_run
from scripts.check_historical_coverage import collect_coverage
from scripts.check_historical_coverage import run as coverage_run

pytestmark = pytest.mark.db


# ─── 1. 空 DB ─────────────────────────────────────────────────────────


async def test_coverage_empty_db(
    db_engine: AsyncEngine,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """空 DB → total=0, pct=0, AC 失败."""
    report = await collect_coverage()
    assert report["total"] == 0
    assert report["industry_pct"] == 0.0
    assert report["first_day_pct"] == 0.0
    assert report["buckets"] == []


# ─── 2. synthetic 跑后 AC 全过 ────────────────────────────────────────


async def test_coverage_after_synthetic_backfill(
    db_engine: AsyncEngine,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """backfill synthetic 600 行 → coverage 全部 100% (synthetic 必填)."""
    code = await backfill_run(
        source="synthetic",
        fixture_file=DEFAULT_FIXTURE_FILE,
        target_rows=600,
        year_from=2022,
        year_to=2025,
        seed=42,
        limit=500,
        dry_run=False,
    )
    assert code == 0

    report = await collect_coverage()
    total = report["total"]
    industry_pct = report["industry_pct"]
    first_day_pct = report["first_day_pct"]
    assert isinstance(total, int) and isinstance(industry_pct, float) and isinstance(first_day_pct, float)
    assert total >= 600
    # synthetic + fixture 都 100% 填了 industry / first_day_change_pct
    assert industry_pct >= 80.0, f"industry_pct={industry_pct} 不达 AC 80%"
    assert first_day_pct >= 60.0, f"first_day_pct={first_day_pct} 不达 AC 60%"


# ─── 3. 多 bucket 分桶 ────────────────────────────────────────────────


async def test_coverage_buckets_grouped_by_data_source(
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """混存 ``backfill-fixture-curated`` + 模拟 ``backfill-akshare-2026`` (后者 industry NULL)
    → buckets 列表分两桶, 各 pct 独立计算."""
    # 先跑 fixture 写 40 行 (全 100%)
    await backfill_run(
        source="fixture",
        fixture_file=DEFAULT_FIXTURE_FILE,
        target_rows=0,
        year_from=2022,
        year_to=2025,
        seed=42,
        limit=500,
        dry_run=False,
    )

    # 再手插 5 行模拟 akshare (industry / first_day_change_pct 都 NULL, 模拟 ingest 缺字段)
    async with db_engine.begin() as conn:
        for i in range(5):
            await conn.execute(
                text(
                    """
                    INSERT INTO ipos (code, name, market, status, data_source, listing_date)
                    VALUES (:code, :name, 'A', 'listed',
                            'backfill-akshare-2026', '2024-06-01')
                    """
                ),
                {"code": f"688{i:03d}.SH", "name": f"测试-{i}"},
            )

    report = await collect_coverage()
    buckets_obj = report["buckets"]
    assert isinstance(buckets_obj, list)
    by_source = {b["data_source"]: b for b in buckets_obj}

    # fixture 桶: 100%
    assert by_source["backfill-fixture-curated"]["industry_pct"] == 100.0
    assert by_source["backfill-fixture-curated"]["first_day_pct"] == 100.0

    # akshare 桶: 0% (industry / first_day 都 NULL)
    assert by_source["backfill-akshare-2026"]["count"] == 5
    assert by_source["backfill-akshare-2026"]["industry_pct"] == 0.0
    assert by_source["backfill-akshare-2026"]["first_day_pct"] == 0.0

    # 总体: 40 行 fixture (全过) + 5 行 akshare (全空) = 88.9% / 88.9%
    industry_pct = report["industry_pct"]
    first_day_pct = report["first_day_pct"]
    assert isinstance(industry_pct, float) and isinstance(first_day_pct, float)
    assert 80.0 <= industry_pct <= 100.0


# ─── 4. threshold 动态 pass / fail ────────────────────────────────────


async def test_coverage_threshold_pass_fail(
    db_engine: AsyncEngine,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """fixture 100% 跑过, --industry-min 110 (故意 > 100) 必失败 returns 1."""
    await backfill_run(
        source="fixture",
        fixture_file=DEFAULT_FIXTURE_FILE,
        target_rows=0,
        year_from=2022,
        year_to=2025,
        seed=42,
        limit=500,
        dry_run=False,
    )

    # AC 80/60 → pass
    code_ok = await coverage_run(industry_min=80.0, first_day_min=60.0, fmt="text")
    assert code_ok == 0, "fixture 100% 应满足 AC 80/60"

    # 故意把 threshold 设 110 (> 100% max) → fail
    code_fail = await coverage_run(
        industry_min=110.0,  # 不可能达到
        first_day_min=60.0,
        fmt="text",
    )
    assert code_fail == 1, "industry_min=110 必失败"


# ─── 5. JSON 格式输出 ────────────────────────────────────────────────


async def test_coverage_json_format(
    db_engine: AsyncEngine,  # noqa: ARG001
    truncate_all: None,  # noqa: ARG001
    patch_session_factory: None,  # noqa: ARG001
) -> None:
    """--format json: stdout 是合法 JSON + 含 passed / buckets / 各 pct 字段."""
    await backfill_run(
        source="fixture",
        fixture_file=DEFAULT_FIXTURE_FILE,
        target_rows=0,
        year_from=2022,
        year_to=2025,
        seed=42,
        limit=500,
        dry_run=False,
    )

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = await coverage_run(
            industry_min=80.0, first_day_min=60.0, fmt="json"
        )
    assert code == 0

    payload = json.loads(buf.getvalue())
    assert payload["passed"] is True
    assert payload["total"] >= 30
    assert payload["industry_pct"] == 100.0
    assert payload["first_day_pct"] == 100.0
    assert payload["ac_industry_min_pct"] == 80.0
    assert payload["ac_first_day_min_pct"] == 60.0
    assert isinstance(payload["buckets"], list)
    assert len(payload["buckets"]) >= 1
    assert payload["buckets"][0]["data_source"] == "backfill-fixture-curated"
