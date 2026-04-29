"""历史 IPO 数据 coverage 检查脚本 (QA-S5-001 BC-1/2/7 清零).

把 ``ipos`` 表 ``status='listed'`` 的行按 ``data_source`` 分桶, 输出每桶
``industry_l1 not null %`` / ``first_day_change_pct not null %`` 等字段覆盖率,
判断是否满足 spec/12 §QA-S5-001 AC:

- ``industry_l1 not null ratio ≥ 80%``
- ``first_day_change_pct not null ratio ≥ 60%``

使用方式
========

    cd apps/api

    # 检查现状 + 退出码 0 (达标) / 1 (未达标)
    uv run python -m scripts.check_historical_coverage

    # 自定义阈值
    uv run python -m scripts.check_historical_coverage \\
        --industry-min 90 --first-day-min 70

    # JSON 输出 (给 CI / dashboard 接)
    uv run python -m scripts.check_historical_coverage --format json

输出例
======

::

    Historical IPO coverage report (status='listed' only)
    ════════════════════════════════════════════════════════
    data_source                       count  industry%  first_day%
    ────────────────────────────────────────────────────────
    backfill-fixture-curated             40     100.0%     100.0%
    synthetic-2026                      560     100.0%     100.0%
    backfill-akshare-2026                15      33.3%       0.0%
    NULL                                  3       0.0%       0.0%
    ────────────────────────────────────────────────────────
    TOTAL                               618      96.6%      97.4%
    ════════════════════════════════════════════════════════
    AC industry_l1 not null ≥ 80%   : ✅ 96.6%
    AC first_day_change_pct ≥ 60%   : ✅ 97.4%

退出码
======

- ``0``: 全部 AC 满足
- ``1``: 至少一项 AC 不满足 (CI 据此 fail)
- ``2``: DB 连接 / 查询失败
- ``3``: 参数非法
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import text as sa_text

from app.core.logging import logger
from app.db import get_session_factory

DEFAULT_INDUSTRY_MIN = 80.0
DEFAULT_FIRST_DAY_MIN = 60.0


async def collect_coverage() -> dict[str, object]:
    """聚合 ``ipos`` 表 listed 行的 coverage; 返扁平字典给上层 format.

    SQL 走单条 GROUP BY + ROLLUP 等价 (用 UNION ALL 显式总计行, 同一 round trip).
    走 ``status='listed'`` 过滤; upcoming / subscribing / withdrawn 行不算 coverage.
    """
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            sa_text(
                """
                SELECT
                    COALESCE(data_source, 'NULL') AS bucket,
                    COUNT(*) AS total,
                    COUNT(industry_l1) AS industry_filled,
                    COUNT(first_day_change_pct) AS first_day_filled
                FROM ipos
                WHERE status = 'listed'
                GROUP BY data_source
                ORDER BY total DESC
                """
            )
        )
        buckets: list[dict[str, object]] = []
        total = 0
        industry_filled = 0
        first_day_filled = 0
        for row in result.fetchall():
            buckets.append(
                {
                    "data_source": row.bucket,
                    "count": int(row.total),
                    "industry_filled": int(row.industry_filled),
                    "first_day_filled": int(row.first_day_filled),
                    "industry_pct": (
                        100.0 * row.industry_filled / row.total
                        if row.total > 0
                        else 0.0
                    ),
                    "first_day_pct": (
                        100.0 * row.first_day_filled / row.total
                        if row.total > 0
                        else 0.0
                    ),
                }
            )
            total += int(row.total)
            industry_filled += int(row.industry_filled)
            first_day_filled += int(row.first_day_filled)

    return {
        "buckets": buckets,
        "total": total,
        "industry_filled": industry_filled,
        "first_day_filled": first_day_filled,
        "industry_pct": (100.0 * industry_filled / total) if total > 0 else 0.0,
        "first_day_pct": (100.0 * first_day_filled / total) if total > 0 else 0.0,
    }


def _format_text(report: dict[str, object], industry_min: float, first_day_min: float) -> str:
    """ASCII art 报表; 给 ops 直接 cat 看用."""
    buckets = report["buckets"]
    assert isinstance(buckets, list)

    lines = [
        "Historical IPO coverage report (status='listed' only)",
        "═" * 64,
        f"{'data_source':32s} {'count':>6s} {'industry%':>10s} {'first_day%':>11s}",
        "─" * 64,
    ]
    for b in buckets:
        lines.append(
            f"{str(b['data_source']):32s} {b['count']:>6d} "
            f"{b['industry_pct']:>9.1f}% {b['first_day_pct']:>10.1f}%"
        )
    lines.append("─" * 64)
    lines.append(
        f"{'TOTAL':32s} {report['total']:>6d} "
        f"{report['industry_pct']:>9.1f}% {report['first_day_pct']:>10.1f}%"
    )
    lines.append("═" * 64)

    industry_pct = report["industry_pct"]
    first_day_pct = report["first_day_pct"]
    assert isinstance(industry_pct, float) and isinstance(first_day_pct, float)
    industry_ok = "✅" if industry_pct >= industry_min else "❌"
    first_day_ok = "✅" if first_day_pct >= first_day_min else "❌"
    lines.append(
        f"AC industry_l1 not null ≥ {industry_min:g}%   : "
        f"{industry_ok} {industry_pct:.1f}%"
    )
    lines.append(
        f"AC first_day_change_pct ≥ {first_day_min:g}%   : "
        f"{first_day_ok} {first_day_pct:.1f}%"
    )
    return "\n".join(lines)


def _check_pass(
    report: dict[str, object], industry_min: float, first_day_min: float
) -> bool:
    industry_pct = report["industry_pct"]
    first_day_pct = report["first_day_pct"]
    assert isinstance(industry_pct, float) and isinstance(first_day_pct, float)
    return industry_pct >= industry_min and first_day_pct >= first_day_min


async def run(*, industry_min: float, first_day_min: float, fmt: str) -> int:
    try:
        report = await collect_coverage()
    except Exception as e:  # noqa: BLE001
        logger.exception(f"check_historical_coverage.query_failed err={e}")
        return 2

    passed = _check_pass(report, industry_min, first_day_min)

    if fmt == "json":
        print(
            json.dumps(
                {
                    **report,
                    "ac_industry_min_pct": industry_min,
                    "ac_first_day_min_pct": first_day_min,
                    "passed": passed,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(_format_text(report, industry_min, first_day_min))

    return 0 if passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="历史 IPO 数据 coverage 检查 (QA-S5-001 BC-1/2/7)"
    )
    parser.add_argument(
        "--industry-min",
        type=float,
        default=DEFAULT_INDUSTRY_MIN,
        help=f"industry_l1 not null 最低 % (默认 {DEFAULT_INDUSTRY_MIN:g})",
    )
    parser.add_argument(
        "--first-day-min",
        type=float,
        default=DEFAULT_FIRST_DAY_MIN,
        help=f"first_day_change_pct not null 最低 % (默认 {DEFAULT_FIRST_DAY_MIN:g})",
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="输出格式: text (默认, 给运营) / json (给 CI / dashboard)",
    )
    args = parser.parse_args(argv)

    if not (0 <= args.industry_min <= 100 and 0 <= args.first_day_min <= 100):
        logger.error("--industry-min / --first-day-min 必须在 [0, 100] 范围")
        return 3

    return asyncio.run(
        run(
            industry_min=args.industry_min,
            first_day_min=args.first_day_min,
            fmt=args.format,
        )
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
