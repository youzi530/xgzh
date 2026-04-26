"""``python -m evals.cli`` CLI 入口.

用法
----
::

    # 1. smoke (无 IO, CI 跑这个验证数据集本身没坏)
    uv run python -m evals.cli --mode keyword

    # 2. retrieval baseline (需 PG, 评 RAG 召回@5)
    uv run python -m evals.cli --mode retrieval

    # 3. 端到端 + LLM-as-judge (需 PG + LLM key)
    uv run python -m evals.cli --mode end_to_end --use-judge

    # 4. 部分 case 调试
    uv run python -m evals.cli --mode retrieval --cases BASIC_001,RAG_005

输出
----
- 控制台打印 markdown 报告 (摘要 + by_category + 失败 case)
- 落盘 ``evals/reports/eval-<sprint>-<mode>-<ts>.json/.md``
- exit code: 评测自身错误 → 1; 指标低于阈值 (``--fail-below-recall`` 等) → 2
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import logger
from evals.reporter import render_markdown, write_report
from evals.runner import run_dataset


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        prog="evals.cli",
        description="XGZH 离线评测脚手架 (BE-S2-009).",
    )
    parser.add_argument(
        "--dataset",
        default=settings.eval_dataset_path,
        help=f"评测集 JSONL 路径 (默认 {settings.eval_dataset_path})",
    )
    parser.add_argument(
        "--mode",
        choices=["keyword", "retrieval", "end_to_end"],
        default="keyword",
        help="评测模式: keyword (无 IO smoke) / retrieval (PG) / end_to_end (PG+LLM)",
    )
    parser.add_argument(
        "--sprint",
        default="sprint2",
        help="报告 metadata 标记 (默认 sprint2).",
    )
    parser.add_argument(
        "--cases",
        default="",
        help="只跑指定 case_id 列表 (逗号分隔), 默认跑全集.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=settings.eval_judge_concurrency,
        help=f"并发上限 (默认 {settings.eval_judge_concurrency}).",
    )
    parser.add_argument(
        "--use-judge",
        action="store_true",
        default=False,
        help="end_to_end 模式时是否调 LLM-as-judge (默认关).",
    )
    parser.add_argument(
        "--out-dir",
        default=settings.eval_report_dir,
        help=f"报告输出目录 (默认 {settings.eval_report_dir}).",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        default=False,
        help="只打印报告, 不落盘 (CI smoke 用).",
    )
    parser.add_argument(
        "--fail-below-recall",
        type=float,
        default=None,
        help="召回@5 低于此值则 exit code = 2 (CI 阈值告警, 留 QA-S2-002 用)",
    )
    parser.add_argument(
        "--fail-above-hallucination",
        type=float,
        default=None,
        help="幻觉率高于此值则 exit code = 2.",
    )
    return parser.parse_args(argv)


async def _amain(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cases_filter = (
        [c.strip() for c in args.cases.split(",") if c.strip()]
        if args.cases
        else None
    )

    try:
        report = await run_dataset(
            dataset_path=args.dataset,
            mode=args.mode,
            sprint=args.sprint,
            use_judge=args.use_judge,
            concurrency=args.concurrency,
            cases_filter=cases_filter,
        )
    except FileNotFoundError as e:
        logger.error(f"eval.cli.dataset_missing: {e}")
        print(f"[ERROR] 数据集不存在: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        logger.error(f"eval.cli.dataset_invalid: {e}")
        print(f"[ERROR] 数据集不合法: {e}", file=sys.stderr)
        return 1

    md = render_markdown(report)
    print(md)

    if not args.no_write:
        json_path, md_path = write_report(report, out_dir=args.out_dir)
        print(f"\n[OK] 报告已落盘:\n  - {json_path}\n  - {md_path}")

    summary = report.summary
    failed_threshold = False

    if (
        args.fail_below_recall is not None
        and summary.recall_at_5 is not None
        and summary.recall_at_5 < args.fail_below_recall
    ):
        print(
            f"\n[FAIL] 召回@5 = {summary.recall_at_5:.3f} 低于阈值 "
            f"{args.fail_below_recall}",
            file=sys.stderr,
        )
        failed_threshold = True

    if (
        args.fail_above_hallucination is not None
        and summary.hallucination_rate is not None
        and summary.hallucination_rate > args.fail_above_hallucination
    ):
        print(
            f"\n[FAIL] 幻觉率 = {summary.hallucination_rate:.3f} 高于阈值 "
            f"{args.fail_above_hallucination}",
            file=sys.stderr,
        )
        failed_threshold = True

    return 2 if failed_threshold else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main"]


# 防止某些 Python 环境对 ``__main__`` import 时缺少模块路径的兜底
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
