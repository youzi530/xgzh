"""引用源装配 (BE-S2-007 Tool Use 第 3 层 - citation pipeline).

把 ``hybrid_search`` Tool 返回的 chunk 列表 + LLM 输出文本两侧合起来, 装配
spec/04 §3.3 §C 要求的"引用强制校验"基础数据:

输入
====
- ``hybrid_search_results: list[dict]``: ``hybrid_search`` Tool 单次或多次调用
  返回的 results 累积 (每个 dict 含 chunk_id / doc_id / ipo_code / page / text /
  score / ...)
- ``answer_text: str``: LLM 最终给出的 markdown 文本 (含 ``[1] [2]`` 这种引用标记)

输出
====
- ``CitationBundle{citations, validated_text}``:
  - ``citations``: 给端层 SSE 的 sources 数组, 每项形如 ``{"idx":1,"chunk_id":..,
    "doc_id":..,"ipo_code":..,"page":..,"snippet":"前 200 字预览"}``
  - ``validated_text``: 把 LLM 文本里"超出 sources 长度"的 ``[N]`` 标记移除后的文本
    (spec/04 §3.3 §C "幻觉风险: 引用了不存在的来源" 防御)

设计取舍
========
- **以"工具内出现顺序"作为引用编号 [1][2][3]…的真相**: LLM 写 ``[1]`` 时它
  心里其实是 hybrid_search 第几次调用的第几条 chunk, 但 LLM 会乱标. 端层
  统一按 chunk dedup (按 ``chunk_id``) 去重 + 出现顺序编号给 LLM, 让它在
  system prompt 里看到 ``hybrid_search 已为你装配 [1][2][3]…`` 然后照编号引
- **dedup**: 同一 chunk_id 只编一次号. hybrid_search 多轮调用结果可能重叠
- **幻觉防御**: ``[N]`` 中 N > len(citations) → strip 掉; 不仅 logger.warning
  也直接从 answer 里移除, 避免端层 SSE 透出"虚假引用"误导用户
- **不在本层做** prompt 改写 / 重新调用 LLM (那是 spec/04 §3.3 §B, 走 forbidden
  patterns + ensure_disclaimer 已有, 与本层正交)
- **不依赖 LangGraph 状态**: 纯函数, 输入 list + str, 输出 dict + str. 让端层
  / 单测可以脱离 graph.py 直接调

snippet 截断
============
- 每条 citation 携带前 200 字预览 (中英文混算 codepoint), 用 ``…`` 截尾
- 端层 SSE 给前端一个轻量 "原文片段抽屉"基础 (FE-S2-003 引用源面板会用)
- 不做高亮 / 关键词裁切: spec/03 §引用源面板已要求"原文呈现", FE 可在前端做
  高亮; 后端只保证拿到 stable snippet
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import logger

_CITATION_RE = re.compile(r"\[(\d+)\]")
_SNIPPET_MAX_CHARS = 200


@dataclass(frozen=True, slots=True)
class Citation:
    """单条引用源, 与 spec/09 §BE-S2-001 chat_messages.citations JSONB 对齐."""

    idx: int
    chunk_id: str
    doc_id: str
    ipo_code: str | None
    page: int | None
    snippet: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "idx": self.idx,
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "ipo_code": self.ipo_code,
            "page": self.page,
            "snippet": self.snippet,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class CitationBundle:
    """citation pipeline 输出 (端层 SSE / DB 落表都用这个)."""

    citations: list[Citation] = field(default_factory=list)
    validated_text: str = ""
    # 防幻觉的诊断信息: 命中过的非法引用编号 (1-based). 端层不一定要 SSE 但
    # logger.warning 会打, 单测断言用
    invalid_citation_indices: list[int] = field(default_factory=list)


def _truncate_snippet(text: str, max_chars: int = _SNIPPET_MAX_CHARS) -> str:
    """按 codepoint 截断, 末尾 ``…``. 空 / None 容忍."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def _dedup_chunks(
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按 ``chunk_id`` 去重 (保持第一次出现顺序)."""
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for c in chunks:
        cid = str(c.get("chunk_id", ""))
        if not cid or cid in seen:
            continue
        seen.add(cid)
        deduped.append(c)
    return deduped


def build_citations(
    hybrid_search_results: list[dict[str, Any]],
) -> list[Citation]:
    """把 ``hybrid_search`` Tool 多次调用结果合并 → 1-based 编号 ``Citation``.

    使用方约定: 调用方按主循环里 hybrid_search 出现的顺序 ``extend`` 进 list,
    本函数只做 dedup + 编号. 不重排. 不做 score 排序 (顺序 = 引用编号, 让 LLM
    心智一致).
    """
    deduped = _dedup_chunks(hybrid_search_results)
    citations: list[Citation] = []
    for i, c in enumerate(deduped, start=1):
        try:
            score = float(c.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        citations.append(
            Citation(
                idx=i,
                chunk_id=str(c.get("chunk_id", "")),
                doc_id=str(c.get("doc_id", "")),
                ipo_code=c.get("ipo_code"),
                page=c.get("page"),
                snippet=_truncate_snippet(str(c.get("text", ""))),
                score=score,
            )
        )
    return citations


def validate_citations_in_text(
    text: str,
    citations: list[Citation],
) -> tuple[str, list[int]]:
    """spec/04 §3.3 §C: ``[N]`` 中 N > len(citations) → strip 掉.

    返回 ``(validated_text, invalid_indices_seen)``:
    - 命中合法引用: 保留 ``[N]``
    - 命中非法引用: 整段 ``[N]`` 删除 (不留空 ``[]``)
    - 没有任何引用: 直接返回原文
    """
    if not text:
        return "", []

    invalid_seen: list[int] = []
    n_citations = len(citations)

    def _replace(match: re.Match[str]) -> str:
        n = int(match.group(1))
        if 1 <= n <= n_citations:
            return match.group(0)
        invalid_seen.append(n)
        return ""

    cleaned = _CITATION_RE.sub(_replace, text)

    if invalid_seen:
        logger.warning(
            f"agent.citation.invalid_indices count={len(invalid_seen)} "
            f"max_valid={n_citations} hits={invalid_seen[:10]}"
        )
    return cleaned, invalid_seen


def assemble(
    *,
    hybrid_search_results: list[dict[str, Any]],
    answer_text: str,
) -> CitationBundle:
    """citation pipeline 一站式入口: 装配 + 校验 + 输出 ``CitationBundle``.

    端层 ``api/v1/chat.py`` 在 LangGraph 主循环结束后调一次本函数, 拿到的
    ``CitationBundle`` 同时落 ``chat_messages.citations`` 和 SSE ``sources``
    事件.
    """
    citations = build_citations(hybrid_search_results)
    validated_text, invalid = validate_citations_in_text(answer_text, citations)
    return CitationBundle(
        citations=citations,
        validated_text=validated_text,
        invalid_citation_indices=invalid,
    )


__all__ = [
    "Citation",
    "CitationBundle",
    "assemble",
    "build_citations",
    "validate_citations_in_text",
]
