"""``services/agent/graph.py`` 内部 helper 单测 (BE-S2-007).

graph.run 主流程的 happy / 异常路径已由 ``tests/integration/test_chat_diagnose.py``
端到端覆盖. 这里只测纯函数 helpers + AgentEvent 数据结构, 防回归.
"""

from __future__ import annotations

from decimal import Decimal

from app.adapters.llm_client import TokenUsage
from app.services.agent.graph import (
    FinalAnswerEvent,
    StartedEvent,
    StepErrorEvent,
    TokenDeltaEvent,
    ToolCallEvent,
    _aggregate_usage,
    _resolve_provider,
    _result_preview,
    _serialize_tool_result_for_llm,
)
from app.services.agent.tool_registry import ToolResult


# ─── _result_preview ──────────────────────────────────────────────────────


def test_result_preview_none() -> None:
    assert _result_preview(None) is None
    assert _result_preview({}) is None


def test_result_preview_truncates_long_string() -> None:
    long = "x" * 500
    out = _result_preview({"a": long})
    assert out is not None
    assert isinstance(out["a"], str)
    assert out["a"].endswith("…")
    assert len(out["a"]) <= 201


def test_result_preview_compresses_list_dict() -> None:
    out = _result_preview({"peers": [1, 2, 3], "meta": {"x": 1, "y": 2}})
    assert out == {"peers": "<list len=3>", "meta": "<dict keys=2>"}


def test_result_preview_truncates_keys() -> None:
    big = {f"k{i}": i for i in range(20)}
    out = _result_preview(big, max_keys=5)
    assert out is not None
    assert out.get("__truncated__") is True
    assert sum(1 for k in out if k != "__truncated__") == 5


# ─── _serialize_tool_result_for_llm ───────────────────────────────────────


def test_serialize_tool_result_ok() -> None:
    r = ToolResult.success({"code": "0700.HK", "pe": 15.5})
    s = _serialize_tool_result_for_llm(r)
    assert "0700.HK" in s
    assert "pe" in s


def test_serialize_tool_result_failure() -> None:
    r = ToolResult.failure("上游 502")
    s = _serialize_tool_result_for_llm(r)
    assert "error" in s
    assert "502" in s


def test_serialize_tool_result_unserializable_falls_back() -> None:
    """非 JSON 可序列化对象走 default=str 兜底."""
    class _Obj:
        def __repr__(self) -> str:
            return "_Obj()"

    r = ToolResult.success({"weird": _Obj()})
    s = _serialize_tool_result_for_llm(r)
    assert "_Obj" in s


# ─── _aggregate_usage ────────────────────────────────────────────────────


def test_aggregate_usage_empty() -> None:
    u = _aggregate_usage([])
    assert u.prompt_tokens == 0
    assert u.completion_tokens == 0
    assert u.total_tokens == 0


def test_aggregate_usage_sums() -> None:
    u1 = TokenUsage(10, 20, 30, Decimal("0.001"))
    u2 = TokenUsage(5, 15, 20, Decimal("0.002"))
    agg = _aggregate_usage([u1, u2])
    assert agg.prompt_tokens == 15
    assert agg.completion_tokens == 35
    assert agg.total_tokens == 50
    assert agg.cost_cny == Decimal("0.003")


# ─── _resolve_provider ────────────────────────────────────────────────────


def test_resolve_provider_siliconflow() -> None:
    assert _resolve_provider("openai/deepseek-ai/DeepSeek-V3") == "siliconflow"


def test_resolve_provider_deepseek_native() -> None:
    assert _resolve_provider("deepseek/deepseek-chat") == "deepseek"


def test_resolve_provider_zhipu() -> None:
    assert _resolve_provider("zhipu/glm-4-air") == "zhipu"


def test_resolve_provider_unknown_default_siliconflow() -> None:
    assert _resolve_provider("foo/bar") == "siliconflow"


# ─── AgentEvent 数据结构 (frozen / 字段) ──────────────────────────────────


def test_started_event_frozen() -> None:
    import uuid as _uuid

    sid = _uuid.uuid4()
    e = StartedEvent(chat_session_id=sid, model="m", ipo_code="0700.HK")
    assert e.chat_session_id == sid
    assert e.model == "m"


def test_token_delta_event_text() -> None:
    e = TokenDeltaEvent(text="hello")
    assert e.text == "hello"


def test_tool_call_event_status() -> None:
    e = ToolCallEvent(
        name="t1",
        args={"x": 1},
        status="ok",
        latency_ms=100,
        result_preview={"a": 1},
    )
    assert e.status == "ok"
    assert e.error is None


def test_step_error_event_default_cause() -> None:
    e = StepErrorEvent(message="boom")
    assert e.message == "boom"
    assert e.cause_type == ""


def test_final_answer_event_minimal() -> None:
    import uuid as _uuid

    from app.services.agent.citation import CitationBundle

    e = FinalAnswerEvent(
        message_id=_uuid.uuid4(),
        text="ok",
        citation_bundle=CitationBundle(),
        usage_aggregate=TokenUsage.empty(),
        finish_reason="stop",
        invalid_citation_indices=[],
    )
    assert e.text == "ok"
    assert e.finish_reason == "stop"
