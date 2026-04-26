"""``services/agent/citation.py`` 单测 (BE-S2-007).

覆盖
====
- ``build_citations``: dedup 按 chunk_id, 编号按出现顺序, 字段映射 (text →
  snippet, score 类型容错)
- ``validate_citations_in_text``: ``[N]`` 中 N 越界 → strip; 合法保留;
  无引用直接返回原文
- ``assemble``: 一站式 + invalid_citation_indices 透出
- snippet 截断: > 200 char 末尾 ``…``
"""

from __future__ import annotations

from app.services.agent.citation import (
    Citation,
    assemble,
    build_citations,
    validate_citations_in_text,
)


def _chunk(
    chunk_id: str,
    *,
    doc_id: str = "prospectus-0700",
    ipo_code: str | None = "0700.HK",
    page: int | None = 12,
    text: str = "公司去年营收同比 +35%, 主要由 SaaS 业务带动 ...",
    score: float = 0.85,
) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "ipo_code": ipo_code,
        "page": page,
        "text": text,
        "score": score,
    }


# ─── build_citations ──────────────────────────────────────────────────────


def test_build_citations_basic_order() -> None:
    chunks = [_chunk("c-1"), _chunk("c-2"), _chunk("c-3")]
    cites = build_citations(chunks)
    assert [c.idx for c in cites] == [1, 2, 3]
    assert [c.chunk_id for c in cites] == ["c-1", "c-2", "c-3"]
    assert all(isinstance(c, Citation) for c in cites)


def test_build_citations_dedup_keeps_first_occurrence() -> None:
    chunks = [_chunk("c-1"), _chunk("c-2"), _chunk("c-1"), _chunk("c-3")]
    cites = build_citations(chunks)
    assert [c.idx for c in cites] == [1, 2, 3]
    assert [c.chunk_id for c in cites] == ["c-1", "c-2", "c-3"]


def test_build_citations_skips_empty_chunk_id() -> None:
    chunks = [_chunk(""), _chunk("c-1"), {"text": "no chunk_id here"}]
    cites = build_citations(chunks)
    assert [c.chunk_id for c in cites] == ["c-1"]


def test_build_citations_score_type_coercion() -> None:
    chunks = [
        {**_chunk("c-1"), "score": "0.7"},  # str → float
        {**_chunk("c-2"), "score": None},  # None → 0.0
        {**_chunk("c-3"), "score": float("nan")},  # nan 仍可以 float
    ]
    cites = build_citations(chunks)
    assert cites[0].score == 0.7
    assert cites[1].score == 0.0
    # nan != nan, 用 score == score 判
    assert cites[2].score != cites[2].score or isinstance(cites[2].score, float)


def test_build_citations_snippet_truncation() -> None:
    long_text = "A" * 250 + " 末尾"
    chunks = [_chunk("c-1", text=long_text)]
    cites = build_citations(chunks)
    assert cites[0].snippet.endswith("…")
    assert len(cites[0].snippet) <= 201


def test_build_citations_short_text_no_ellipsis() -> None:
    chunks = [_chunk("c-1", text="短文本")]
    cites = build_citations(chunks)
    assert cites[0].snippet == "短文本"


# ─── validate_citations_in_text ───────────────────────────────────────────


def test_validate_keeps_valid_citations() -> None:
    cites = build_citations([_chunk("c-1"), _chunk("c-2")])
    text = "公司营收 [1] 上升, 风险参考 [2] 板块."
    cleaned, invalid = validate_citations_in_text(text, cites)
    assert cleaned == text
    assert invalid == []


def test_validate_strips_out_of_range() -> None:
    cites = build_citations([_chunk("c-1"), _chunk("c-2")])
    text = "看 [1] 和 [3] 还有 [99] 这些"
    cleaned, invalid = validate_citations_in_text(text, cites)
    assert "[3]" not in cleaned
    assert "[99]" not in cleaned
    assert "[1]" in cleaned
    assert sorted(invalid) == [3, 99]


def test_validate_empty_text() -> None:
    cites = build_citations([_chunk("c-1")])
    cleaned, invalid = validate_citations_in_text("", cites)
    assert cleaned == ""
    assert invalid == []


def test_validate_no_citations_passthrough() -> None:
    text = "没有引用编号的文本"
    cleaned, invalid = validate_citations_in_text(text, [])
    assert cleaned == text
    assert invalid == []


def test_validate_all_invalid_strips_all() -> None:
    text = "完全错误的引用 [1] [2] [3]"
    cleaned, invalid = validate_citations_in_text(text, [])
    # 全部删除后允许多余空格
    for n in (1, 2, 3):
        assert f"[{n}]" not in cleaned
    assert sorted(invalid) == [1, 2, 3]


# ─── assemble (端到端) ─────────────────────────────────────────────────────


def test_assemble_happy() -> None:
    chunks = [_chunk("c-1"), _chunk("c-2"), _chunk("c-1")]  # 含一条重复
    text = "营收增长 [1], 风险点 [2]; [3] 越界."
    bundle = assemble(hybrid_search_results=chunks, answer_text=text)
    assert len(bundle.citations) == 2  # dedup
    assert "[3]" not in bundle.validated_text
    assert bundle.invalid_citation_indices == [3]


def test_assemble_no_results() -> None:
    bundle = assemble(hybrid_search_results=[], answer_text="无引用文本")
    assert bundle.citations == []
    assert bundle.validated_text == "无引用文本"
    assert bundle.invalid_citation_indices == []


def test_citation_to_dict_field_set() -> None:
    cites = build_citations([_chunk("c-1")])
    d = cites[0].to_dict()
    assert set(d) == {
        "idx",
        "chunk_id",
        "doc_id",
        "ipo_code",
        "page",
        "snippet",
        "score",
    }
    assert d["idx"] == 1
    assert d["chunk_id"] == "c-1"
    assert d["doc_id"] == "prospectus-0700"
    assert d["ipo_code"] == "0700.HK"
    assert d["page"] == 12
