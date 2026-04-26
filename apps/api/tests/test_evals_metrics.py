"""``evals.metrics`` 单测 (BE-S2-009).

覆盖:
- ``compute_recall_at_5``:
    - 空 chunks → False, 空命中
    - 命中 expected_doc_ids 优先于 keyword
    - keyword substring 命中, casefold 大小写不敏感
    - 全 5 都不命中 → False
    - top5 截断: 6 + 个 chunk 只看前 5
    - 空 keywords + 空 doc_ids → False (防止 [] in str 误命中)
- ``extract_atomic_facts``: 数字 / 日期 / 货币 / 百分比 抓取 + 去重
- ``compute_hallucination``:
    - 空答案 → 0.0
    - 全部 fact 在 citations 中 → 0.0
    - 部分 fact 不在 citations → 比例正确
    - 全角 / 半角 / 千分位逗号归一化等价
"""

from __future__ import annotations

from evals.metrics import (
    compute_hallucination,
    compute_recall_at_5,
    extract_atomic_facts,
)

# ─── compute_recall_at_5 ──────────────────────────────────────────────────


def test_recall_empty_chunks_returns_false() -> None:
    hit, matched = compute_recall_at_5([], expected_keywords=["A"])
    assert not hit
    assert matched == []


def test_recall_keyword_substring_hit() -> None:
    chunks = [{"doc_id": "d1", "text": "招股书披露主要业务为社交"}]
    hit, matched = compute_recall_at_5(chunks, expected_keywords=["社交", "电商"])
    assert hit
    assert "社交" in matched
    assert "电商" not in matched


def test_recall_keyword_case_insensitive() -> None:
    chunks = [{"doc_id": "d1", "text": "Tencent Holdings"}]
    hit, matched = compute_recall_at_5(chunks, expected_keywords=["tencent"])
    assert hit
    assert matched == ["tencent"]


def test_recall_no_match_returns_false() -> None:
    chunks = [{"doc_id": "d1", "text": "完全无关的内容"}]
    hit, matched = compute_recall_at_5(chunks, expected_keywords=["游戏", "广告"])
    assert not hit
    assert matched == []


def test_recall_truncates_to_top5() -> None:
    chunks = [{"doc_id": f"d{i}", "text": "无关"} for i in range(5)]
    chunks.append({"doc_id": "d_hit", "text": "包含 关键词 的 chunk"})  # 第 6 个
    hit, _ = compute_recall_at_5(chunks, expected_keywords=["关键词"])
    assert not hit, "top5 截断后第 6 个 chunk 不应参与命中"


def test_recall_doc_id_priority_over_keyword() -> None:
    chunks = [
        {"doc_id": "expected-doc", "text": "无关"},
        {"doc_id": "other", "text": "包含 关键词"},
    ]
    hit, matched = compute_recall_at_5(
        chunks,
        expected_keywords=["关键词"],
        expected_doc_ids=["expected-doc"],
    )
    assert hit
    assert matched == ["expected-doc"]


def test_recall_empty_keywords_and_doc_ids_returns_false() -> None:
    chunks = [{"doc_id": "d1", "text": "any text"}]
    hit, matched = compute_recall_at_5(chunks, expected_keywords=[])
    assert not hit
    assert matched == []


def test_recall_handles_missing_text_field() -> None:
    chunks = [{"doc_id": "d1"}, {"doc_id": "d2", "text": "包含 主营 业务"}]
    hit, matched = compute_recall_at_5(chunks, expected_keywords=["主营"])
    assert hit
    assert matched == ["主营"]


# ─── extract_atomic_facts ────────────────────────────────────────────────


def test_extract_facts_picks_up_dates_and_numbers() -> None:
    text = "公司于 2018 年 9 月 20 日上市, 发行价 69 港元, 募资约 326 亿港元, 涨幅 5.5%。"
    facts = extract_atomic_facts(text)
    assert any("2018年9月20日" in f or "2018 年 9 月 20 日" in f for f in facts)
    assert any("69" in f for f in facts) or any("港元 69" in f for f in facts)
    assert any("5.5%" in f for f in facts)


def test_extract_facts_dedup_normalize() -> None:
    text = "5,000 万 USD 5000万 5000 万"  # 三种写法应归并
    facts = extract_atomic_facts(text)
    # 至少抓到一个数字+单位; 归一化后等价的不重复
    assert len(facts) <= 3
    assert any("万" in f for f in facts)


def test_extract_facts_empty() -> None:
    assert extract_atomic_facts("") == []
    assert extract_atomic_facts("纯文本无任何数字日期") == []


# ─── compute_hallucination ────────────────────────────────────────────────


def test_hallucination_empty_answer_zero() -> None:
    score, unbacked = compute_hallucination(
        answer_text="",
        citations=[{"snippet": "any"}],
        ground_truth_facts=["fact 1"],
    )
    assert score == 0.0
    assert unbacked == []


def test_hallucination_all_backed() -> None:
    answer = "腾讯于 2004 年 6 月 16 日在港交所上市"
    citations = [
        {"snippet": "腾讯控股 2004 年 6 月 16 日在港交所主板挂牌, 发行价 3.70 港元"}
    ]
    score, unbacked = compute_hallucination(
        answer_text=answer,
        citations=citations,
        ground_truth_facts=["2004 年 6 月 16 日"],
    )
    assert score == 0.0
    assert unbacked == []


def test_hallucination_partial_backed() -> None:
    answer = "公司 2004 年上市, 发行价 99 港元, 实际并不存在"
    citations = [{"snippet": "公司 2004 年在港交所上市"}]
    score, unbacked = compute_hallucination(
        answer_text=answer,
        citations=citations,
        ground_truth_facts=["发行价 3.70 港元"],
    )
    # 99 港元 在 citation 中找不到 → 应被 unbacked 抓住
    assert score > 0.0
    assert any("99" in f for f in unbacked)


def test_hallucination_no_atomic_facts_returns_zero() -> None:
    answer = "公司主营业务为软件开发与互联网服务"  # 无数字日期等
    citations = [{"snippet": "completely different"}]
    score, unbacked = compute_hallucination(
        answer_text=answer,
        citations=citations,
        ground_truth_facts=["软件"],
    )
    # 无 atomic facts + ground_truth_facts 都没出现在答案里 → 0.0
    assert score == 0.0
    assert unbacked == []


def test_hallucination_normalizes_thousands_separator() -> None:
    answer = "募资 5,000 万港元"  # 千分位逗号
    citations = [{"snippet": "募资 5000 万港元"}]  # 无逗号
    score, unbacked = compute_hallucination(
        answer_text=answer,
        citations=citations,
        ground_truth_facts=[],
    )
    # 归一化后应当能被认作 backed
    assert score == 0.0, f"unbacked={unbacked}"
