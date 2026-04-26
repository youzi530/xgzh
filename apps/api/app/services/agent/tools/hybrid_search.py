"""``hybrid_search`` Tool — 招股书 RAG 检索 (BE-S2-006b).

把 BE-S2-005 已落地的 ``app.services.rag.hybrid_search.hybrid_search`` 函数包装
成 LangGraph ReAct 主循环可注入的 OpenAI Tool. 检索原语完全复用 (没拷贝代码,
只做 schema + ToolResult 序列化).

为什么单独建一个 Tool 包装层
============================
1. **协议匹配**: ``services/rag/hybrid_search`` 函数签名是
   ``(session, query, *, ipo_code, doc_type, lang, ...) -> HybridSearchOutput``,
   返回的是 frozen dataclass; LLM tool message content 只接受 dict, 必须做
   序列化层
2. **入参收窄**: 检索函数本身接受 9 个调优参数 (top_k / rrf_k / pool_size 等);
   LLM 不需要也不应该看到这些, Tool 入参只暴露 ``query`` / ``ipo_code`` /
   ``doc_type`` / ``lang`` / ``top_k`` 5 个语义级参数. 调优参数全走 settings 默认
3. **session 注入**: BE-S2-005 hybrid_search 需要 ``AsyncSession``; 沙盒装饰器
   ``**deps`` 透传机制让 BE-S2-007 LangGraph 主循环可以传 session; 当前没传时
   内部用 ``get_session_factory()`` 起临时 session, 让 Tool 单测不强依赖主循环
4. **search_articles 占位**: spec/04 §3.1 第 6 个 Tool ``search_articles`` 也是
   "在文档库里找东西", 与 hybrid_search 检索原语一致, 区别只是 doc_type. 真接入
   articles 表后另起 Tool 即可, 当前先把 ``hybrid_search`` 走通

数据契约
========
入参:
- ``query``: 自然语言 query, 必填
- ``ipo_code``: 限定到某只 IPO (强烈建议; 不限会跨股召回, 容易混)
- ``doc_type``: 默认 ``prospectus``; BE-S2-006b 范围内只有招股书; future 加
  ``annual_report`` / ``research_report`` 等
- ``lang``: 默认 None (不限); 招股书有英文 / 简中 / 繁中三种
- ``top_k``: 最终返回 chunk 数, 1-10, 默认 5

出参 ``data``:
```python
{
  "query": "原 query",
  "filter": {"ipo_code": "...", "doc_type": "...", "lang": "..."},
  "results": [
    {
      "chunk_id": "uuid str",
      "doc_id": "...",
      "ipo_code": "...",
      "page": 12,
      "chunk_index": 5,
      "text": "招股书原文片段, ...",
      "score": 0.85,
      "rrf_score": 0.034,
      "vector_rank": 2,
      "bm25_rank": 1,
    },
    ...
  ],
  "stats": {
    "vector_hits": 50, "bm25_hits": 47, "fused_count": 78,
    "reranked": true, "elapsed_ms": 230,
  },
}
```

不在本 Tool 做
==============
- query rewrite / HyDE: BE-S2-007 LangGraph 主循环里干 (有 LLM context)
- 引用源装配 (citation [1][2] 编号 → 源文档): 也是 BE-S2-007 主循环职责;
  本 Tool 只返回原始 chunk, 编号 / 拼接由主循环统一做
- session 事务管理: 检索是 read-only, 调用方决定 session 生命周期
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session_factory
from app.services.agent.sandbox import sandboxed
from app.services.agent.tool_registry import Tool, ToolResult, register
from app.services.rag.hybrid_search import SearchResult, hybrid_search

_TOOL_NAME = "hybrid_search"
_TOOL_DESCRIPTION = (
    "在招股书原文 (prospectus) 中做混合检索（向量 + BM25 + RRF + cross-encoder 重排）。"
    "返回最相关的 top-K 段落，含页码、来源文档 ID 与命中分数；适合回答需要"
    "在招股书具体章节中找事实的提问（财务科目、风险因素、业务描述、竞争格局、"
    "募资用途等）。**强烈建议传 ipo_code** 限定单只 IPO，否则会跨股召回。"
)
_TOOL_TIMEOUT = 15.0  # rerank API call 可能慢; 给宽一点
_DEFAULT_TOP_K = 5
_MAX_TOP_K = 10


class HybridSearchInput(BaseModel):
    """``hybrid_search`` Tool 入参. 调优参数 (top_k 之外的 RRF / pool 等) 不暴露,
    走 settings 默认避免 LLM 误调.
    """

    query: str = Field(
        min_length=1,
        max_length=500,
        description="自然语言 query, 如 '过去三年营业收入' / '主要风险因素'。",
    )
    ipo_code: str | None = Field(
        default=None,
        max_length=16,
        description=(
            "限定单只 IPO (如 ``0700.HK``); 强烈建议提供, 否则会跨股召回。"
        ),
    )
    doc_type: str | None = Field(
        default="prospectus",
        max_length=32,
        description="文档类型筛选, 默认 ``prospectus`` 招股书。",
    )
    lang: str | None = Field(
        default=None,
        max_length=8,
        description="语言筛选 (``zh-CN`` / ``zh-HK`` / ``en``); 不传则不过滤。",
    )
    top_k: int = Field(
        default=_DEFAULT_TOP_K,
        ge=1,
        le=_MAX_TOP_K,
        description=f"返回 chunk 数, 默认 {_DEFAULT_TOP_K}, 最多 {_MAX_TOP_K}。",
    )


def _result_to_dict(r: SearchResult) -> dict[str, Any]:
    """``SearchResult`` (uuid / float) → JSON-friendly dict."""
    return {
        "chunk_id": str(r.chunk_id) if isinstance(r.chunk_id, uuid.UUID) else r.chunk_id,
        "doc_id": r.doc_id,
        "ipo_code": r.ipo_code,
        "page": r.page,
        "chunk_index": r.chunk_index,
        "text": r.text,
        "score": float(r.score),
        "rrf_score": float(r.rrf_score),
        "vector_rank": r.vector_rank,
        "bm25_rank": r.bm25_rank,
    }


async def _do_search(
    args: HybridSearchInput,
    *,
    session: AsyncSession,
) -> ToolResult:
    """实际跑混合检索. session 由调用方注入, 本函数不管生命周期."""
    output = await hybrid_search(
        session,
        args.query,
        ipo_code=args.ipo_code.upper().strip() if args.ipo_code else None,
        doc_type=args.doc_type,
        lang=args.lang,
        final_top_k=args.top_k,
    )

    data: dict[str, Any] = {
        "query": args.query,
        "filter": {
            "ipo_code": args.ipo_code,
            "doc_type": args.doc_type,
            "lang": args.lang,
        },
        "results": [_result_to_dict(r) for r in output.results],
        "stats": dict(output.stats),
    }
    if not output.results:
        data["warning"] = (
            "未在 ipo_documents 表中检索到任何匹配段落; 可能是: "
            "(1) 该 ipo_code 招股书还未入库 (BE-S2-004 流水线); "
            "(2) query 关键词与文档不匹配 (建议换更具体 / 通用的提法); "
            "(3) doc_type / lang 过滤过严。"
        )
    return ToolResult.success(data)


@sandboxed(input_model=HybridSearchInput, timeout_seconds=_TOOL_TIMEOUT)
async def _run(
    args: HybridSearchInput,
    *,
    session: AsyncSession | None = None,
) -> ToolResult:
    """Tool 入口. ``session`` 由 BE-S2-007 主循环 ``runner(args, session=...)`` 注入;
    单测 / 默认情况下走 ``get_session_factory()`` 起临时 session.
    """
    if session is not None:
        return await _do_search(args, session=session)

    factory = get_session_factory()
    async with factory() as managed_session:
        return await _do_search(args, session=managed_session)


register(
    Tool(
        name=_TOOL_NAME,
        description=_TOOL_DESCRIPTION,
        input_model=HybridSearchInput,
        runner=_run,
        timeout_seconds=_TOOL_TIMEOUT,
        tags=("rag", "search"),
    )
)


__all__ = ["HybridSearchInput"]
