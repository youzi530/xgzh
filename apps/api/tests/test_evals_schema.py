"""``evals.schema`` 单测 (BE-S2-009).

覆盖:
- ``EvalCase`` 字段约束 (typo / 缺字段 / 大小写归一化)
- ``load_cases`` 行级错误定位 + ID 重复 + 注释行跳过
- ``build_summary``:
    - keyword mode 全 None 不抛
    - retrieval mode 命中 / 不命中 → recall_at_5 = 50%
    - end_to_end 含 hallucination + judge 字段聚合
- ``dump_cases`` ↔ ``load_cases`` 闭环 (round-trip)
- ``sprint2_80q.jsonl`` 真数据集自检: 80 条 / 4 类各 20 条 / id 唯一 / ipo_code 大写
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from evals.schema import (
    EvalCase,
    EvalCaseResult,
    build_summary,
    dump_cases,
    load_cases,
)

DATASET_PATH = Path(__file__).resolve().parent.parent / "evals/dataset/sprint2_80q.jsonl"


# ─── EvalCase 字段约束 ────────────────────────────────────────────────────


def _base_case_dict(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": "TEST_001",
        "category": "basic",
        "query": "测试 query?",
        "ipo_code": "0700.HK",
        "expected_keywords": ["关键词"],
        "ground_truth_facts": ["事实 1"],
    }
    base.update(overrides)
    return base


def test_eval_case_minimal_valid() -> None:
    case = EvalCase.model_validate(_base_case_dict())
    assert case.id == "TEST_001"
    assert case.category == "basic"
    assert case.expected_doc_ids == []
    assert case.tags == []
    assert case.source == "synthetic-public"


def test_eval_case_normalizes_ipo_code_to_upper() -> None:
    case = EvalCase.model_validate(_base_case_dict(ipo_code="  0700.hk  "))
    assert case.ipo_code == "0700.HK"


def test_eval_case_strips_keywords_and_rejects_all_blank() -> None:
    case = EvalCase.model_validate(
        _base_case_dict(expected_keywords=["  关键词 ", "另一个"])
    )
    assert case.expected_keywords == ["关键词", "另一个"]

    with pytest.raises(ValidationError):
        EvalCase.model_validate(_base_case_dict(expected_keywords=["   ", "\t"]))


def test_eval_case_rejects_unknown_category() -> None:
    with pytest.raises(ValidationError):
        EvalCase.model_validate(_base_case_dict(category="other"))


def test_eval_case_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        EvalCase.model_validate(_base_case_dict(unexpected_field="oops"))


def test_eval_case_requires_keywords_and_ground_truth() -> None:
    with pytest.raises(ValidationError):
        EvalCase.model_validate(_base_case_dict(expected_keywords=[]))
    with pytest.raises(ValidationError):
        EvalCase.model_validate(_base_case_dict(ground_truth_facts=[]))


def test_eval_case_immutable() -> None:
    case = EvalCase.model_validate(_base_case_dict())
    # frozen=True 会在赋值时抛 ValidationError
    with pytest.raises(ValidationError):
        case.id = "X"  # type: ignore[misc]


# ─── load_cases ──────────────────────────────────────────────────────────


def test_load_cases_empty_file_raises(tmp_path: Path) -> None:
    p = tmp_path / "empty.jsonl"
    p.write_text("\n# comment only\n", encoding="utf-8")
    with pytest.raises(ValueError, match="评测集为空"):
        load_cases(p)


def test_load_cases_skips_comment_and_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "with_comments.jsonl"
    import json

    p.write_text(
        "# header comment\n"
        "\n"
        + json.dumps(_base_case_dict(id="C_001"))
        + "\n"
        + "# middle comment\n"
        + json.dumps(_base_case_dict(id="C_002"))
        + "\n",
        encoding="utf-8",
    )
    cases = load_cases(p)
    assert [c.id for c in cases] == ["C_001", "C_002"]


def test_load_cases_dup_id_raises(tmp_path: Path) -> None:
    p = tmp_path / "dup.jsonl"
    import json

    p.write_text(
        json.dumps(_base_case_dict(id="DUP_001"))
        + "\n"
        + json.dumps(_base_case_dict(id="DUP_001"))
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="case id 重复"):
        load_cases(p)


def test_load_cases_invalid_json_locates_line(tmp_path: Path) -> None:
    p = tmp_path / "bad.jsonl"
    p.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="第 1 行"):
        load_cases(p)


def test_load_cases_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        load_cases("/no/such/path.jsonl")


def test_dump_load_roundtrip(tmp_path: Path) -> None:
    cases = [
        EvalCase.model_validate(_base_case_dict(id="RT_001")),
        EvalCase.model_validate(
            _base_case_dict(id="RT_002", category="risk", ipo_code="3690.hk")
        ),
    ]
    p = tmp_path / "rt.jsonl"
    n = dump_cases(cases, p)
    assert n == 2
    loaded = load_cases(p)
    assert [c.id for c in loaded] == ["RT_001", "RT_002"]
    assert loaded[1].ipo_code == "3690.HK"


# ─── build_summary ───────────────────────────────────────────────────────


def _result(
    *,
    case_id: str,
    category: str = "basic",
    mode: str = "retrieval",
    recall: bool | None = None,
    halluc: float | None = None,
    judge: int | None = None,
    error: str = "",
) -> EvalCaseResult:
    return EvalCaseResult(
        case_id=case_id,
        category=category,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        recall_at_5_hit=recall,
        hallucination_score=halluc,
        judge_score=judge,
        error=error,
    )


def test_build_summary_keyword_all_none() -> None:
    cases = [
        _result(case_id="K1", mode="keyword", category="basic"),
        _result(case_id="K2", mode="keyword", category="risk"),
    ]
    s = build_summary(cases)
    assert s.total == 2
    assert s.failed == 0
    assert s.recall_at_5 is None
    assert s.hallucination_rate is None
    assert s.judge_mean_score is None


def test_build_summary_retrieval_recall_50pct() -> None:
    cases = [
        _result(case_id="R1", recall=True, category="basic"),
        _result(case_id="R2", recall=False, category="basic"),
    ]
    s = build_summary(cases)
    assert s.recall_at_5 == 0.5
    assert s.by_category["basic"]["recall_at_5"] == 0.5


def test_build_summary_e2e_full_metrics() -> None:
    cases = [
        _result(case_id="E1", recall=True, halluc=0.0, judge=5, category="rag"),
        _result(case_id="E2", recall=True, halluc=0.5, judge=3, category="rag"),
        _result(case_id="E3", recall=False, halluc=1.0, judge=1, category="rag"),
    ]
    s = build_summary(cases)
    assert s.recall_at_5 is not None
    assert abs(s.recall_at_5 - 2 / 3) < 1e-3
    assert s.hallucination_rate == 0.5
    assert s.judge_mean_score == 3.0


def test_build_summary_failed_excluded_from_means() -> None:
    cases = [
        _result(case_id="F1", recall=True, halluc=0.0, judge=4, error=""),
        _result(case_id="F2", error="boom"),
    ]
    s = build_summary(cases)
    assert s.failed == 1
    assert s.recall_at_5 == 1.0
    assert s.hallucination_rate == 0.0
    assert s.judge_mean_score == 4.0


# ─── 真数据集自检 (sprint2_80q.jsonl) ─────────────────────────────────────


def test_sprint2_dataset_loads_80_cases() -> None:
    cases = load_cases(DATASET_PATH)
    assert len(cases) == 80


def test_sprint2_dataset_4x20_balanced() -> None:
    cases = load_cases(DATASET_PATH)
    cnt: dict[str, int] = {}
    for c in cases:
        cnt[c.category] = cnt.get(c.category, 0) + 1
    assert cnt == {"basic": 20, "risk": 20, "peers": 20, "rag": 20}


def test_sprint2_dataset_ipo_codes_normalized() -> None:
    cases = load_cases(DATASET_PATH)
    for c in cases:
        assert c.ipo_code == c.ipo_code.upper(), f"未归一化: {c.id} {c.ipo_code}"
        assert "." in c.ipo_code, f"ipo_code 缺市场后缀: {c.id} {c.ipo_code}"


def test_sprint2_dataset_unique_ids() -> None:
    cases = load_cases(DATASET_PATH)
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))


def test_sprint2_dataset_query_within_limits() -> None:
    cases = load_cases(DATASET_PATH)
    for c in cases:
        assert 1 <= len(c.query) <= 300, f"query 长度越界: {c.id} {len(c.query)} 字"
        assert c.expected_keywords, f"无关键词: {c.id}"
        assert c.ground_truth_facts, f"无 ground truth: {c.id}"
