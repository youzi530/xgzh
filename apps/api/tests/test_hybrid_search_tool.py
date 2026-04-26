"""``hybrid_search`` Tool 单测 (BE-S2-006b).

覆盖
====
- happy: monkeypatch ``services.rag.hybrid_search`` 函数, 验证 Tool 包装层做对了
  入参映射 (ipo_code 大写归一 / final_top_k / doc_type / lang) + 出参序列化
  (UUID → str, frozen dataclass → dict)
- 空结果 → ToolResult.success + warning
- session 注入 (deps): 主循环传 session 时 Tool 直接用, 不走 session_factory
- session 不传 (默认): 走 ``get_session_factory()`` 起临时 session; monkeypatch
  让它返回 mock factory, 验证调用闭环
- 入参校验失败: query 空 / 超长 / top_k 越界
- OpenAI schema 形状

策略
====
本 Tool 包装层不重测 hybrid_search 算法本身 (BE-S2-005 已有
``test_hybrid_search.py`` 覆盖); 这里 monkeypatch 函数把 SQL / embed / rerank 全
mock 掉, 只验证 Tool 适配层与沙盒.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agent.tool_registry import get
from app.services.agent.tools import hybrid_search as hybrid_search_tool_mod
from app.services.rag.hybrid_search import HybridSearchOutput, SearchResult


def _make_results(n: int = 2) -> list[SearchResult]:
    """造 n 条 SearchResult, chunk_id 用真 UUID."""
    out: list[SearchResult] = []
    for i in range(n):
        out.append(
            SearchResult(
                chunk_id=uuid.uuid4(),
                doc_id="prospectus-0700",
                ipo_code="0700.HK",
                chunk_index=i,
                page=10 + i,
                text=f"测试段落 {i}: 截止 2023 年公司营收 ...",
                score=0.9 - i * 0.1,
                rrf_score=0.034 - i * 0.001,
                vector_rank=i + 1,
                bm25_rank=i + 1 if i < 1 else None,
            )
        )
    return out


def _make_output(results: list[SearchResult]) -> HybridSearchOutput:
    return HybridSearchOutput(
        results=results,
        stats={
            "vector_hits": 50,
            "bm25_hits": 47,
            "fused_count": 78,
            "reranked": True,
            "elapsed_ms": 230,
        },
    )


# ─── happy path ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_search_happy_with_explicit_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式传 session 时, Tool 不应起新 session_factory."""
    captured: dict[str, Any] = {}

    async def fake_hybrid_search(
        session: Any,
        query: str,
        **kwargs: Any,
    ) -> HybridSearchOutput:
        captured["session"] = session
        captured["query"] = query
        captured["kwargs"] = kwargs
        return _make_output(_make_results(2))

    monkeypatch.setattr(hybrid_search_tool_mod, "hybrid_search", fake_hybrid_search)

    # session_factory 被调用就报错: 显式传 session 时不应走 factory
    def _no_factory() -> Any:
        raise AssertionError("session 已显式注入, 不应再调用 get_session_factory()")

    monkeypatch.setattr(hybrid_search_tool_mod, "get_session_factory", _no_factory)

    sentinel_session = object()

    tool = get("hybrid_search")
    assert tool is not None
    r = await tool.runner(
        {"query": "营业收入", "ipo_code": "0700.hk", "top_k": 3},
        session=sentinel_session,
    )

    assert r.ok is True
    assert r.error is None
    assert r.data is not None

    # session 透传
    assert captured["session"] is sentinel_session
    assert captured["query"] == "营业收入"
    # ipo_code 走 upper().strip() 归一
    assert captured["kwargs"]["ipo_code"] == "0700.HK"
    assert captured["kwargs"]["doc_type"] == "prospectus"  # 默认
    assert captured["kwargs"]["lang"] is None
    assert captured["kwargs"]["final_top_k"] == 3

    # 出参形状
    d = r.data
    assert d["query"] == "营业收入"
    assert d["filter"] == {"ipo_code": "0700.hk", "doc_type": "prospectus", "lang": None}
    assert len(d["results"]) == 2
    first = d["results"][0]
    # UUID → str
    assert isinstance(first["chunk_id"], str)
    uuid.UUID(first["chunk_id"])  # 必须是合法 UUID 字符串
    assert first["ipo_code"] == "0700.HK"
    assert first["text"].startswith("测试段落")
    assert isinstance(first["score"], float)
    assert "stats" in d
    assert d["stats"]["reranked"] is True


@pytest.mark.asyncio
async def test_hybrid_search_no_session_uses_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不传 session 时, Tool 应走 ``get_session_factory()`` 起临时 session."""
    fake_session = MagicMock(name="fake_session")

    @asynccontextmanager
    async def _fake_session_cm() -> Any:
        yield fake_session

    fake_factory = MagicMock(side_effect=_fake_session_cm)

    def _factory_provider() -> Any:
        return fake_factory

    captured: dict[str, Any] = {}

    async def fake_hybrid_search(session: Any, query: str, **kwargs: Any) -> HybridSearchOutput:
        captured["session"] = session
        captured["query"] = query
        captured["kwargs"] = kwargs
        return _make_output(_make_results(1))

    monkeypatch.setattr(hybrid_search_tool_mod, "hybrid_search", fake_hybrid_search)
    monkeypatch.setattr(hybrid_search_tool_mod, "get_session_factory", _factory_provider)

    tool = get("hybrid_search")
    assert tool is not None
    r = await tool.runner({"query": "风险因素"})

    assert r.ok is True
    assert r.data is not None
    # session 走 factory 起的 fake_session
    assert captured["session"] is fake_session
    assert captured["query"] == "风险因素"


@pytest.mark.asyncio
async def test_hybrid_search_empty_results_returns_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hybrid_search(*args: Any, **kwargs: Any) -> HybridSearchOutput:
        return _make_output([])  # 空结果

    monkeypatch.setattr(hybrid_search_tool_mod, "hybrid_search", fake_hybrid_search)

    sentinel_session = object()
    tool = get("hybrid_search")
    assert tool is not None
    r = await tool.runner(
        {"query": "不存在的内容"},
        session=sentinel_session,
    )

    assert r.ok is True
    assert r.data is not None
    assert r.data["results"] == []
    assert "warning" in r.data
    assert "未在 ipo_documents" in r.data["warning"]


@pytest.mark.asyncio
async def test_hybrid_search_lang_filter_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_hybrid_search(*args: Any, **kwargs: Any) -> HybridSearchOutput:
        captured.update(kwargs)
        return _make_output([])

    monkeypatch.setattr(hybrid_search_tool_mod, "hybrid_search", fake_hybrid_search)

    sentinel_session = object()
    tool = get("hybrid_search")
    assert tool is not None
    r = await tool.runner(
        {"query": "test", "ipo_code": "0700.HK", "lang": "zh-CN", "doc_type": "annual_report"},
        session=sentinel_session,
    )

    assert r.ok is True
    assert captured["lang"] == "zh-CN"
    assert captured["doc_type"] == "annual_report"


# ─── 入参校验 (沙盒) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_search_missing_query() -> None:
    tool = get("hybrid_search")
    assert tool is not None
    r = await tool.runner({})
    assert r.ok is False
    assert r.error is not None
    assert "参数校验失败" in r.error


@pytest.mark.asyncio
async def test_hybrid_search_empty_query() -> None:
    tool = get("hybrid_search")
    assert tool is not None
    r = await tool.runner({"query": ""})
    assert r.ok is False


@pytest.mark.asyncio
async def test_hybrid_search_top_k_too_large() -> None:
    tool = get("hybrid_search")
    assert tool is not None
    r = await tool.runner({"query": "test", "top_k": 999})
    assert r.ok is False


# ─── 上游异常归一 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hybrid_search_upstream_exception_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_hybrid_search(*args: Any, **kwargs: Any) -> HybridSearchOutput:
        raise RuntimeError("embed API 503")

    monkeypatch.setattr(hybrid_search_tool_mod, "hybrid_search", fake_hybrid_search)

    sentinel_session = object()
    tool = get("hybrid_search")
    assert tool is not None
    r = await tool.runner({"query": "test"}, session=sentinel_session)
    assert r.ok is False
    assert r.error is not None
    assert "RuntimeError" in r.error


# ─── OpenAI schema ────────────────────────────────────────────────────────


def test_hybrid_search_openai_schema_shape() -> None:
    tool = get("hybrid_search")
    assert tool is not None
    schema = tool.to_openai_schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "hybrid_search"
    assert "招股书" in fn["description"] or "检索" in fn["description"]
    params = fn["parameters"]
    assert "query" in params["properties"]
    assert "query" in params["required"]
    assert "ipo_code" in params["properties"]
    assert "top_k" in params["properties"]
    # title 字段必须被提前剔除 (LLM 兼容)
    assert "title" not in params
    # 防 "AsyncMock 未使用"导致 lint 抱怨
    _ = AsyncMock
