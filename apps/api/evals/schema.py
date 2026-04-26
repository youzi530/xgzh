"""评测数据集 / 报告的 Pydantic schema + JSONL 读写工具.

为什么用 Pydantic 而不是 dataclass
==================================
- 数据集是手工标注 + 跨 PR 维护的高频改动文件, 字段 typo / 漏写比代码多得多
- Pydantic ``model_validate`` 直接给"第 N 条 case 字段 X 缺失"的精确报错, 比
  dataclass ``__post_init__`` 友好 10 倍
- 评测报告要序列化成 JSON / markdown 双形态, ``model_dump`` 一行解决

字段约束 (BE-S2-009)
====================
- ``category`` 限 4 类 (basic / risk / peers / rag), 与 spec/09 §S2 评测分桶一致
- ``ipo_code`` 用 ``XXXX.HK`` 大写格式, 跟 ``ipo_documents.ipo_code`` 一致便于
  ``hybrid_search`` 强 filter
- ``expected_keywords`` 至少 1 个: 召回@5 命中以"top5 chunk text 任一含任一
  keyword"为标准, 如果 0 个 keyword 这条 case 无法计算 recall, 必须挡掉
- ``ground_truth_facts`` 至少 1 个: 端到端 LLM-as-judge 跑评分时拿这个当
  reference, 0 条会让 judge 退化成"自由作答评估", 噪声大
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EvalCategory = Literal["basic", "risk", "peers", "rag"]
EvalMode = Literal["keyword", "retrieval", "end_to_end"]


class EvalCase(BaseModel):
    """单条评测样本 (JSONL 一行 = 一条 EvalCase).

    手写约束
    --------
    - ``id`` 形如 ``BASIC_001`` / ``RISK_017``, 全集合内唯一; ``runner`` 加载时强
      校验 dedup
    - ``query`` 1-300 字, 模拟真实用户在前端聊天框的输入
    - ``expected_keywords`` 用于 ``召回@5`` 命中口径: top5 chunk text 任一包含任一
      keyword 即算命中. *合成* / *公开* 数据来源 → 不依赖真实 chunk_id, 让评测集
      可在任何 PG 数据快照上跑通 (不强绑某次 ingest)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=32)
    category: EvalCategory
    query: str = Field(min_length=1, max_length=300)
    ipo_code: str = Field(min_length=4, max_length=16)
    expected_keywords: list[str] = Field(min_length=1, max_length=20)
    ground_truth_facts: list[str] = Field(min_length=1, max_length=20)
    expected_doc_ids: list[str] = Field(default_factory=list, max_length=20)
    reference_answer: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=10)
    source: str = Field(default="synthetic-public", max_length=64)

    @field_validator("ipo_code")
    @classmethod
    def _normalize_ipo_code(cls, v: str) -> str:
        # 全部 upper + strip, 与 hybrid_search Tool 入口一致, 避免 0700.hk 漏召
        return v.strip().upper()

    @field_validator("expected_keywords")
    @classmethod
    def _strip_keywords(cls, v: list[str]) -> list[str]:
        cleaned = [k.strip() for k in v if k and k.strip()]
        if not cleaned:
            raise ValueError("expected_keywords 不能全为空白")
        return cleaned


class EvalCaseResult(BaseModel):
    """单条 case 的评测结果. 三种 mode 字段并集; 缺失字段 None."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    category: EvalCategory
    mode: EvalMode

    # ── 召回 (mode=retrieval / end_to_end) ──────────────────────────────
    retrieved_chunks: list[dict[str, Any]] = Field(default_factory=list)
    recall_at_5_hit: bool | None = None
    matched_keywords: list[str] = Field(default_factory=list)

    # ── 端到端 (mode=end_to_end) ─────────────────────────────────────────
    answer_text: str = ""
    citations: list[dict[str, Any]] = Field(default_factory=list)
    invalid_citation_indices: list[int] = Field(default_factory=list)
    hallucination_facts: list[str] = Field(default_factory=list)
    hallucination_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="0=完全有据 / 1=完全幻觉; 端到端模式才有",
    )
    judge_score: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="LLM-as-judge 1-5 分; 端到端模式且 LLM 可用才有",
    )
    judge_rationale: str = ""

    # ── 通用 ────────────────────────────────────────────────────────────
    error: str = ""
    elapsed_ms: int = 0


class RunSummary(BaseModel):
    """整轮评测的聚合指标."""

    model_config = ConfigDict(extra="forbid")

    total: int
    succeeded: int
    failed: int

    recall_at_5: float | None = None
    hallucination_rate: float | None = None
    judge_mean_score: float | None = None
    by_category: dict[str, dict[str, float | int | None]] = Field(default_factory=dict)


class RunReport(BaseModel):
    """整轮评测产出物 (落 reports/eval-<sprint>-<ts>.json + .md)."""

    model_config = ConfigDict(extra="forbid")

    sprint: str
    mode: EvalMode
    dataset_path: str
    started_at: datetime
    finished_at: datetime
    summary: RunSummary
    cases: list[EvalCaseResult]

    @classmethod
    def new(
        cls,
        *,
        sprint: str,
        mode: EvalMode,
        dataset_path: str,
        started_at: datetime,
        cases: list[EvalCaseResult],
    ) -> RunReport:
        finished_at = datetime.now(tz=UTC)
        summary = build_summary(cases)
        return cls(
            sprint=sprint,
            mode=mode,
            dataset_path=dataset_path,
            started_at=started_at,
            finished_at=finished_at,
            summary=summary,
            cases=cases,
        )


# ─── JSONL 读写 ────────────────────────────────────────────────────────────


def load_cases(path: str | Path) -> list[EvalCase]:
    """读取 JSONL, 返回 ``EvalCase`` 列表; ID 重复或字段不合法直接抛 ``ValueError``.

    校验顺序: 行号 + Pydantic ValidationError → 用户拿到的报错信息能直接定位
    "evals/dataset/sprint2_80q.jsonl 第 23 行 expected_keywords 缺失".
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"评测集文件不存在: {p}")

    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    with p.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{p}:第 {lineno} 行 JSON 解析失败: {e}") from e
            try:
                case = EvalCase.model_validate(obj)
            except Exception as e:
                raise ValueError(f"{p}:第 {lineno} 行 schema 不合法: {e}") from e
            if case.id in seen_ids:
                raise ValueError(f"{p}:第 {lineno} 行 case id 重复: {case.id}")
            seen_ids.add(case.id)
            cases.append(case)

    if not cases:
        raise ValueError(f"{p}: 评测集为空")
    return cases


def dump_cases(cases: Iterable[EvalCase], path: str | Path) -> int:
    """JSONL 落盘 (主要给生成器用; 不在评测 runtime 走). 返回写入行数."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("w", encoding="utf-8") as f:
        for c in cases:
            f.write(c.model_dump_json(exclude_defaults=False) + "\n")
            n += 1
    return n


def iter_cases_by_category(
    cases: list[EvalCase], category: EvalCategory
) -> Iterator[EvalCase]:
    yield from (c for c in cases if c.category == category)


# ─── 聚合工具 (留给 reporter / runner 两边复用) ────────────────────────────


def build_summary(cases: list[EvalCaseResult]) -> RunSummary:
    """从 ``EvalCaseResult`` 列表算 ``RunSummary``.

    指标口径 (与 spec/09 §S2 §评测集一致):
    - **recall_at_5**: 排除 ``recall_at_5_hit is None`` (即 keyword mode 不算这个),
      余下 ``mean(hit)``
    - **hallucination_rate**: 排除 ``hallucination_score is None``, 余下
      ``mean(score)``; 0 = 完全有据, 1 = 完全幻觉
    - **judge_mean_score**: 排除 ``judge_score is None``, 余下 ``mean``
    - **by_category**: 同 4 类 ``basic / risk / peers / rag`` 各算一份, 让 PM 能
      看出"哪类 query 翻车", 不只看总分
    """
    total = len(cases)
    failed = sum(1 for c in cases if c.error)
    succeeded = total - failed

    def _mean_filter(values: list[float | None]) -> float | None:
        clean = [v for v in values if v is not None]
        return round(sum(clean) / len(clean), 4) if clean else None

    recall_values: list[float | None] = [
        (1.0 if c.recall_at_5_hit else 0.0) if c.recall_at_5_hit is not None else None
        for c in cases
    ]
    halluc_values: list[float | None] = [c.hallucination_score for c in cases]
    judge_values: list[float | None] = [
        float(c.judge_score) if c.judge_score is not None else None for c in cases
    ]

    by_category: dict[str, dict[str, float | int | None]] = {}
    for cat in ("basic", "risk", "peers", "rag"):
        cat_cases = [c for c in cases if c.category == cat]
        if not cat_cases:
            continue
        by_category[cat] = {
            "total": len(cat_cases),
            "failed": sum(1 for c in cat_cases if c.error),
            "recall_at_5": _mean_filter(
                [
                    (1.0 if c.recall_at_5_hit else 0.0)
                    if c.recall_at_5_hit is not None
                    else None
                    for c in cat_cases
                ]
            ),
            "hallucination_rate": _mean_filter(
                [c.hallucination_score for c in cat_cases]
            ),
            "judge_mean_score": _mean_filter(
                [
                    float(c.judge_score) if c.judge_score is not None else None
                    for c in cat_cases
                ]
            ),
        }

    return RunSummary(
        total=total,
        succeeded=succeeded,
        failed=failed,
        recall_at_5=_mean_filter(recall_values),
        hallucination_rate=_mean_filter(halluc_values),
        judge_mean_score=_mean_filter(judge_values),
        by_category=by_category,
    )


__all__ = [
    "EvalCase",
    "EvalCaseResult",
    "EvalCategory",
    "EvalMode",
    "RunReport",
    "RunSummary",
    "build_summary",
    "dump_cases",
    "iter_cases_by_category",
    "load_cases",
]
