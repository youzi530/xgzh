"""``POST /api/v1/chat/diagnose`` E2E (BE-S2-007).

覆盖
====
- happy: 单步 LLM 直接回答, 无 tool 调用 → start / delta+ / end + disclaimer
- 工具调用 (get_ipo_basic_info): 第一步 LLM 决策调 tool, 第二步 LLM 写最终文本
  → chat_tool_calls 落表 + SSE 含 tool_call + chat_messages user/assistant 完整
- LLM 上游异常: 第一步抛 LLMProviderError → SSE event=error + end (ok=false),
  user message 仍落 audit log
- 续聊: 第二请求带 session_id → history_messages 注入到 LLM (验证传给 mock LLM
  的 messages 含上一轮 assistant)

策略
====
- mock ``llm_client.astream_chat_with_meta``: 用 ``Decorator`` 模式让单测
  按调用次数返回不同 stream (第一次 tool_calls / 第二次最终文本)
- DB 走真 Postgres (集成测一致), tools 注册中心走默认 (BE-S2-006a/b 全部 6 个),
  ``get_ipo_basic_info`` Tool 真打 ``ipo_service.get_ipo``, 走真 DB 表
- 不 mock hybrid_search (本套测试不让 LLM 调它); 那会另起一组用例
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters import llm_client
from app.adapters.llm_client import (
    ChatStreamChunk,
    LLMProviderError,
    TokenUsage,
)
from app.db.models.chat import (
    ChatMessage,
    ChatSession,
    ChatTokenUsage,
    ChatToolCall,
)
from app.db.models.ipo import IPO


# ─── 测试夹具: 可编程的 streaming LLM mock ────────────────────────────────


@dataclass
class _StreamScript:
    """单次 LLM 调用的脚本: 先 yield 若干 delta, 最后 yield finish + (usage|tool_calls)."""

    deltas: list[str] = field(default_factory=list)
    finish_reason: str = "stop"
    tool_calls: list[dict[str, Any]] | None = None
    usage_prompt: int = 50
    usage_completion: int = 80


@pytest.fixture
async def fake_streaming_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[
    tuple[Callable[[_StreamScript], None], list[list[dict[str, Any]]]]
]:
    """返回 ``(push_script, captured_messages_per_call)``.

    每调一次 ``push_script(_StreamScript)`` 入队 1 个 LLM 响应脚本; 主循环
    每发起一次 LLM 调用按 FIFO 弹出对应脚本. 没脚本 → 抛 RuntimeError 让
    测试失败 (避免静默死循环).

    captured_messages_per_call: 每次 LLM 调用入参 ``messages`` 的快照, 让
    测试可断言 history 注入正确.
    """
    queue: list[_StreamScript] = []
    captured: list[list[dict[str, Any]]] = []

    def push(script: _StreamScript) -> None:
        queue.append(script)

    async def fake_astream(
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int = 1500,
    ) -> AsyncIterator[ChatStreamChunk]:
        captured.append([dict(m) for m in messages])
        if not queue:
            raise RuntimeError(
                "fake_streaming_llm: 测试未提供 LLM 脚本但主循环又调了一次 LLM"
            )
        script = queue.pop(0)
        for d in script.deltas:
            yield ChatStreamChunk(delta=d)
        yield ChatStreamChunk(
            finish_reason=script.finish_reason,
            usage=TokenUsage(
                prompt_tokens=script.usage_prompt,
                completion_tokens=script.usage_completion,
                total_tokens=script.usage_prompt + script.usage_completion,
                cost_cny=Decimal("0.001"),
            ),
            tool_calls=script.tool_calls,
        )

    monkeypatch.setattr(llm_client, "astream_chat_with_meta", fake_astream)
    # 还要 patch 通过 graph.py 的 import 路径, 防 module 已经把符号拷到自己 namespace
    from app.services.agent import graph as graph_mod

    # graph 模块用 ``llm_client.astream_chat_with_meta`` 通过 module 访问, 不需要再 patch.
    # 但保留这个 import 防触发 lazy 加载 (主循环 import 的是 llm_client 整模块).
    assert graph_mod is not None
    yield push, captured


@pytest.fixture
async def llm_credential_envs(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """硬塞一个假的 SILICONFLOW_API_KEY, 让 graph 主循环不在"无 key"分支提前 return.

    fake_streaming_llm 不真打远程, 这里只让 ``has_llm_credential`` 为真.
    走 ``app.core.config.get_settings`` 缓存 → monkeypatch 直接 set attr.
    """
    from app.core.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "siliconflow_api_key", "test-fake-key")
    yield


# ─── 工具: SSE 解析 ──────────────────────────────────────────────────────


def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """把 sse_starlette 的输出切成 (event, payload) list.

    sse_starlette 用 ``\\r\\n`` 作为行分隔, 事件块之间是 ``\\r\\n\\r\\n``.
    心跳行 ``: ping`` 不在测试期出现 (ping=15s).
    """
    events: list[tuple[str, dict[str, Any]]] = []
    # 兼容 \r\n\r\n 与 \n\n
    normalized = raw.replace("\r\n", "\n")
    blocks = [b for b in normalized.split("\n\n") if b.strip()]
    for blk in blocks:
        event_type: str | None = None
        data_lines: list[str] = []
        for line in blk.splitlines():
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if event_type is None or not data_lines:
            continue
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            payload = {"_raw": "\n".join(data_lines)}
        events.append((event_type, payload))
    return events


# ─── 测试: 无 tool 直接给最终回答 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_diagnose_no_tool_happy_path(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_streaming_llm: tuple[
        Callable[[_StreamScript], None], list[list[dict[str, Any]]]
    ],
    llm_credential_envs: None,  # noqa: ARG001
) -> None:
    """LLM 一步给文本, 不调任何 tool. 验证 SSE / DB 落表."""
    push, captured = fake_streaming_llm
    push(
        _StreamScript(
            deltas=[
                "**基本面摘要**\n",
                "腾讯控股, PE ~15, 行业领先.\n",
                "**风险点**\n  1. 行业波动\n",
            ],
            finish_reason="stop",
        )
    )

    response = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "请诊断 0700.HK", "ipo_code": "0700.HK"},
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)

    types = [e[0] for e in events]
    assert types[0] == "start"
    assert "delta" in types
    assert types[-1] == "end"
    assert "tool_call" not in types
    assert "sources" not in types  # 没 hybrid_search → 没 citations

    # 把 delta 拼起来验证 disclaimer
    delta_text = "".join(e[1]["content"] for e in events if e[0] == "delta")
    assert "腾讯" in delta_text or "PE" in delta_text  # mock 内容透传

    end_event = next(e for e in events if e[0] == "end")
    assert end_event[1]["finish_reason"] == "stop"
    assert end_event[1]["usage"]["prompt_tokens"] == 50
    assert end_event[1]["usage"]["completion_tokens"] == 80

    # DB 落表验证
    async with session_factory() as s:
        sessions = (await s.execute(select(ChatSession))).scalars().all()
        assert len(sessions) == 1
        assert sessions[0].ipo_code == "0700.HK"

        msgs = (
            (
                await s.execute(
                    select(ChatMessage).order_by(ChatMessage.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[0].content == "请诊断 0700.HK"
        assert "不构成投资建议" in msgs[1].content  # disclaimer 兜底
        assert msgs[1].citations is None  # 没 hybrid_search

        usages = (await s.execute(select(ChatTokenUsage))).scalars().all()
        assert len(usages) >= 1
        assert sum(u.input_tokens for u in usages) == 50

        tool_calls = (await s.execute(select(ChatToolCall))).scalars().all()
        assert tool_calls == []

    # 验证 LLM 至少被调用一次, system prompt 含红线
    assert len(captured) >= 1
    sys_msg = captured[0][0]
    assert sys_msg["role"] == "system"
    assert "不构成投资建议" in sys_msg["content"]


# ─── 测试: 工具调用 (get_ipo_basic_info) → 第二步给最终回答 ────────────────


@pytest.mark.asyncio
async def test_chat_diagnose_with_tool_call(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_streaming_llm: tuple[
        Callable[[_StreamScript], None], list[list[dict[str, Any]]]
    ],
    llm_credential_envs: None,  # noqa: ARG001
) -> None:
    """LLM 第一步 tool_calls=[get_ipo_basic_info], 第二步给最终回答."""
    push, captured = fake_streaming_llm

    # 先在 ipos 表灌一条
    async with session_factory() as s:
        s.add(
            IPO(
                code="0700.HK",
                name="腾讯控股",
                market="HK",
                industry_l1="互联网",
                issue_price=Decimal("3.70"),
                issue_currency="HKD",
                pe_ratio=Decimal("15.5"),
                status="listed",
                data_source="seed",
            )
        )
        await s.commit()

    push(
        _StreamScript(
            deltas=[],  # tool_choice=auto 时 content 通常为空
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call_abc123",
                    "type": "function",
                    "function": {
                        "name": "get_ipo_basic_info",
                        "arguments": json.dumps({"code": "0700.HK"}),
                    },
                }
            ],
        )
    )
    push(
        _StreamScript(
            deltas=["腾讯控股 PE 约 15.5, 估值合理.\n"],
            finish_reason="stop",
        )
    )

    response = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "诊断 0700.HK", "ipo_code": "0700.HK"},
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    types = [e[0] for e in events]

    assert types[0] == "start"
    assert "tool_call" in types
    assert types[-1] == "end"

    tc_event = next(e for e in events if e[0] == "tool_call")
    assert tc_event[1]["name"] == "get_ipo_basic_info"
    assert tc_event[1]["status"] == "ok"
    assert tc_event[1]["latency_ms"] >= 0

    delta_text = "".join(e[1]["content"] for e in events if e[0] == "delta")
    assert "腾讯" in delta_text or "估值" in delta_text

    # DB 验证
    async with session_factory() as s:
        # chat_tool_calls 一行 ok
        tcs = (await s.execute(select(ChatToolCall))).scalars().all()
        assert len(tcs) == 1
        assert tcs[0].tool_name == "get_ipo_basic_info"
        assert tcs[0].status == "ok"
        assert tcs[0].latency_ms is not None

        # chat_messages: user → assistant(anchor) → tool → assistant(final)
        msgs = (
            (
                await s.execute(
                    select(ChatMessage).order_by(ChatMessage.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        roles = [m.role for m in msgs]
        assert roles[0] == "user"
        assert "tool" in roles
        assert roles.count("assistant") >= 1
        assert "不构成投资建议" in msgs[-1].content

    # 第二次 LLM 调用应能看到 tool message
    assert len(captured) == 2
    second_call = captured[1]
    tool_role_msgs = [m for m in second_call if m.get("role") == "tool"]
    assert len(tool_role_msgs) == 1
    assert tool_role_msgs[0]["tool_call_id"] == "call_abc123"


# ─── 测试: 上游 LLM 抛错 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_diagnose_llm_provider_error(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    llm_credential_envs: None,  # noqa: ARG001
) -> None:
    """第一步 LLM 抛 LLMProviderError → SSE event=error + end ok=false.

    user message 仍落 audit log (诊断断点用).
    """

    async def boom(*args: Any, **kwargs: Any) -> AsyncIterator[ChatStreamChunk]:
        if False:
            yield  # type: ignore[unreachable]
        raise LLMProviderError(
            "stream_chat call failed: 502 Bad Gateway",
            provider="siliconflow",
            model="openai/test",
        )

    monkeypatch.setattr(llm_client, "astream_chat_with_meta", boom)

    response = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "诊断 0700.HK", "ipo_code": "0700.HK"},
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    types = [e[0] for e in events]

    assert "start" in types
    assert "error" in types
    assert types[-1] == "end"

    err = next(e for e in events if e[0] == "error")
    assert "stream_chat call failed" in err[1]["message"] or "502" in err[1]["message"]

    # user message 仍落表 (audit log)
    async with session_factory() as s:
        msgs = (await s.execute(select(ChatMessage))).scalars().all()
        roles = [m.role for m in msgs]
        assert roles.count("user") == 1
        # 不应有 final assistant message
        assert roles.count("assistant") == 0


# ─── 测试: 续聊 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_diagnose_continuation(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_streaming_llm: tuple[
        Callable[[_StreamScript], None], list[list[dict[str, Any]]]
    ],
    llm_credential_envs: None,  # noqa: ARG001
) -> None:
    """第一轮起新会话, 第二轮带 session_id → history 注入到 LLM messages."""
    push, captured = fake_streaming_llm
    push(_StreamScript(deltas=["第一轮答复"], finish_reason="stop"))

    r1 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "第一问"},
    )
    assert r1.status_code == 200
    events1 = _parse_sse(r1.text)
    start1 = next(e for e in events1 if e[0] == "start")
    session_id = start1[1]["session_id"]
    uuid.UUID(session_id)  # 形状校验

    # 第二轮
    push(_StreamScript(deltas=["第二轮答复"], finish_reason="stop"))
    r2 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "第二问", "session_id": session_id},
    )
    assert r2.status_code == 200
    events2 = _parse_sse(r2.text)
    start2 = next(e for e in events2 if e[0] == "start")
    # 同一 session
    assert start2[1]["session_id"] == session_id

    # 第二次 LLM 调用应该能在 messages 里看到第一轮的 user / assistant
    second_messages = captured[1]
    user_roles = [m for m in second_messages if m.get("role") == "user"]
    assert len(user_roles) >= 2  # 上一轮 user + 本轮 user
    # 第一条非 system 应该是上一轮 user
    non_system = [m for m in second_messages if m.get("role") != "system"]
    assert non_system[0]["content"] == "第一问"


# ─── 测试: 引用源装配 (mock hybrid_search 注入 chunks) ─────────────────────


@pytest.mark.asyncio
async def test_chat_diagnose_with_hybrid_search_citations(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_streaming_llm: tuple[
        Callable[[_StreamScript], None], list[list[dict[str, Any]]]
    ],
    monkeypatch: pytest.MonkeyPatch,
    llm_credential_envs: None,  # noqa: ARG001
) -> None:
    """LLM 调 hybrid_search → graph 装配 citations → SSE event=sources."""
    push, _captured = fake_streaming_llm

    # mock hybrid_search 返回固定 2 条 chunk
    from app.services.agent.tools import hybrid_search as hybrid_search_tool_mod
    from app.services.rag.hybrid_search import HybridSearchOutput, SearchResult

    fake_results = [
        SearchResult(
            chunk_id=uuid.uuid4(),
            doc_id="prospectus-0700",
            ipo_code="0700.HK",
            chunk_index=0,
            page=12,
            text="公司截止 2024 年 Q3 营收同比 +15%, 主要由 SaaS 业务带动",
            score=0.92,
            rrf_score=0.034,
            vector_rank=1,
            bm25_rank=2,
        ),
        SearchResult(
            chunk_id=uuid.uuid4(),
            doc_id="prospectus-0700",
            ipo_code="0700.HK",
            chunk_index=1,
            page=24,
            text="风险因素: 监管不确定性可能导致部分业务下线",
            score=0.81,
            rrf_score=0.029,
            vector_rank=3,
            bm25_rank=1,
        ),
    ]

    async def fake_hybrid_search(
        session: Any, query: str, **kwargs: Any
    ) -> HybridSearchOutput:
        return HybridSearchOutput(
            results=fake_results,
            stats={"vector_hits": 50, "bm25_hits": 47, "fused_count": 78,
                   "reranked": False, "elapsed_ms": 80},
        )

    monkeypatch.setattr(hybrid_search_tool_mod, "hybrid_search", fake_hybrid_search)

    # 第一步 LLM: tool_calls=[hybrid_search]
    push(
        _StreamScript(
            deltas=[],
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call_hs1",
                    "type": "function",
                    "function": {
                        "name": "hybrid_search",
                        "arguments": json.dumps(
                            {"query": "营收增长", "ipo_code": "0700.HK"}
                        ),
                    },
                }
            ],
        )
    )
    # 第二步 LLM: 写最终回答, 引用 [1] (合法) 和 [5] (越界)
    push(
        _StreamScript(
            deltas=[
                "公司营收同比 +15% [1], ",
                "存在监管风险 [2]. ",
                "另见 [5] 越界引用.",
            ],
            finish_reason="stop",
        )
    )

    response = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "营收和风险", "ipo_code": "0700.HK"},
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    types = [e[0] for e in events]

    assert "tool_call" in types
    assert "sources" in types
    assert types[-1] == "end"

    sources_event = next(e for e in events if e[0] == "sources")
    assert len(sources_event[1]["citations"]) == 2
    assert sources_event[1]["citations"][0]["idx"] == 1
    assert sources_event[1]["citations"][0]["page"] == 12
    assert "营收" in sources_event[1]["citations"][0]["snippet"]

    # delta 拼回来 [5] 应被剥除
    delta_text = "".join(e[1]["content"] for e in events if e[0] == "delta")
    # 注意 delta 还包含 [1] [2] 因为它们合法
    assert "[1]" in delta_text
    assert "[2]" in delta_text
    # [5] 在最终落表 + end event 都被标为 invalid
    end_event = next(e for e in events if e[0] == "end")
    assert end_event[1]["invalid_citation_indices"] == [5]

    # DB: chat_messages.citations 落表 = 2 条
    async with session_factory() as s:
        msgs = (await s.execute(select(ChatMessage))).scalars().all()
        final_assistant = [m for m in msgs if m.role == "assistant" and m.citations]
        assert len(final_assistant) == 1
        assert len(final_assistant[0].citations) == 2  # type: ignore[arg-type]
        # validated_text 不应再含 [5]
        assert "[5]" not in final_assistant[0].content
        assert "[1]" in final_assistant[0].content
