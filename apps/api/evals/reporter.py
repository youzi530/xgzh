"""把 ``RunReport`` 渲染成 markdown 报告 + JSON 落盘.

为什么不用 jinja2
=================
报告模板很简单 (3 张表 + by_category 分项), f-string 拼接 30 行就完了, 引一个
jinja2 进来给评测脚手架 build 期再加一个轮子不值. 后续真要可定制再升 jinja2.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from evals.schema import EvalCaseResult, RunReport, RunSummary


def _fmt_pct(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:.{digits}f}%"


def _fmt_score(v: float | None, digits: int = 2) -> str:
    if v is None:
        return "n/a"
    return f"{v:.{digits}f}"


def _summary_block(summary: RunSummary, mode: str) -> str:
    """整体指标段落."""
    lines = [
        "| 指标 | 取值 |",
        "|------|------|",
        f"| 评测模式 | `{mode}` |",
        f"| 总 case 数 | {summary.total} |",
        f"| 成功 | {summary.succeeded} |",
        f"| 失败 | {summary.failed} |",
        f"| 召回@5 | {_fmt_pct(summary.recall_at_5)} |",
        f"| 幻觉率 (字符级 baseline) | {_fmt_pct(summary.hallucination_rate)} |",
        f"| LLM-as-judge 平均分 (1-5) | {_fmt_score(summary.judge_mean_score)} |",
    ]
    return "\n".join(lines)


def _by_category_block(summary: RunSummary) -> str:
    """分类细分指标表."""
    if not summary.by_category:
        return "_(无分类数据)_"
    lines = [
        "| 类别 | 总数 | 失败 | 召回@5 | 幻觉率 | judge mean |",
        "|------|------|------|--------|--------|------------|",
    ]
    for cat in ("basic", "risk", "peers", "rag"):
        d = summary.by_category.get(cat)
        if d is None:
            continue
        recall = d.get("recall_at_5")
        halluc = d.get("hallucination_rate")
        judge = d.get("judge_mean_score")
        lines.append(
            f"| {cat} | {d.get('total')} | {d.get('failed')} | "
            f"{_fmt_pct(float(recall) if recall is not None else None)} | "
            f"{_fmt_pct(float(halluc) if halluc is not None else None)} | "
            f"{_fmt_score(float(judge) if judge is not None else None)} |"
        )
    return "\n".join(lines)


def _failed_cases_block(cases: list[EvalCaseResult], limit: int = 10) -> str:
    failed = [c for c in cases if c.error]
    if not failed:
        return "_无_"
    lines = [
        f"显示前 {min(len(failed), limit)} 条 (共 {len(failed)} 条):",
        "",
        "| case_id | category | error |",
        "|---------|----------|-------|",
    ]
    for c in failed[:limit]:
        err = c.error.replace("\n", " ").strip()
        if len(err) > 120:
            err = err[:120] + "…"
        lines.append(f"| {c.case_id} | {c.category} | {err} |")
    return "\n".join(lines)


def _hallucinated_cases_block(cases: list[EvalCaseResult], limit: int = 10) -> str:
    """挑 ``hallucination_score`` 最高的 N 条列出."""
    halluc = [c for c in cases if c.hallucination_score is not None]
    if not halluc:
        return "_无幻觉 / 未跑端到端模式_"
    halluc.sort(key=lambda c: (c.hallucination_score or 0.0), reverse=True)
    top = [c for c in halluc if (c.hallucination_score or 0.0) > 0.0][:limit]
    if not top:
        return "_所有 case 字符级幻觉 score 均为 0.0_"
    lines = [
        f"显示 hallucination_score > 0 的前 {len(top)} 条:",
        "",
        "| case_id | category | score | unbacked_facts | judge_score |",
        "|---------|----------|-------|----------------|-------------|",
    ]
    for c in top:
        facts = ", ".join((c.hallucination_facts or [])[:3])
        if len(facts) > 100:
            facts = facts[:100] + "…"
        lines.append(
            f"| {c.case_id} | {c.category} | {_fmt_score(c.hallucination_score)} | "
            f"{facts or '-'} | {c.judge_score or '-'} |"
        )
    return "\n".join(lines)


def render_markdown(report: RunReport) -> str:
    """主渲染入口. 渲染顺序: 元信息 → 总览 → 分类 → 失败 → 幻觉 top."""
    started = report.started_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    finished = report.finished_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    duration_s = max(
        0.0,
        (report.finished_at - report.started_at).total_seconds(),
    )

    parts = [
        f"# XGZH 离线评测报告 — {report.sprint}",
        "",
        f"- 评测模式: `{report.mode}`",
        f"- 数据集: `{report.dataset_path}`",
        f"- 开始时间: {started}",
        f"- 结束时间: {finished}",
        f"- 总耗时: {duration_s:.1f}s",
        "",
        "## 总览",
        "",
        _summary_block(report.summary, report.mode),
        "",
        "## 分类细分",
        "",
        _by_category_block(report.summary),
        "",
        "## 失败 case",
        "",
        _failed_cases_block(report.cases),
        "",
        "## 字符级幻觉 top",
        "",
        _hallucinated_cases_block(report.cases),
        "",
    ]
    return "\n".join(parts)


def write_report(
    report: RunReport,
    *,
    out_dir: str | Path = "evals/reports",
    name_prefix: str = "eval",
) -> tuple[Path, Path]:
    """落盘报告. 返回 ``(json_path, markdown_path)``.

    时间戳精确到秒, 防同沙盘多次跑覆盖. 文件名:
    ``<prefix>-<sprint>-<mode>-<YYYYMMDD-HHMMSS>.{json,md}``
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{name_prefix}-{report.sprint}-{report.mode}-{ts}"

    json_path = out / f"{base}.json"
    md_path = out / f"{base}.md"

    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


__all__ = ["render_markdown", "write_report"]
