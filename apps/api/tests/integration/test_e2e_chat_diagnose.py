"""QA-S2-001: Agent 端到端集成测试 (金线打通 BE-S2-007/008 主链路).

定位
====
与 BE-S2-007 单点集成测 (``test_chat_diagnose.py``) 和 BE-S2-008 配额测
(``test_chat_diagnose_quota.py``) 互补:

- 单点测验"协议契约 + 单一行为", 数据集小, mock 重
- 这里测"主链路串联 + 多点协同", 验证从 HTTP 入口到 DB 落盘的完整契约,
  特别关注几个跨 PR 边界容易出问题的链路:
    1. **金线 happy path**: 注册 → ReAct 多 tool 串联 (hybrid_search → basic_info)
       → 引用源装配 → 续聊 history 注入 → DB 落 chat_sessions / chat_messages /
       chat_tool_calls / chat_token_usage 完整
    2. **tool 失败沙盒兜底**: 底层 ipo_service 抛异常时, ToolResult.failure 被
       SSE event=tool_call (status=error) 透传, 但**不**冒成 SSE error 整流
       中断; LLM 第二步看到 tool error 仍能给 final
    3. **forbidden_pattern_filter 端到端**: LLM 输出违规词, 主循环替换为
       "[已合规过滤]" 后落 chat_messages; 同时 disclaimer 兜底
    4. **匿名链路 + IP 配额**: 不带 token 也能用, chat_sessions.user_id IS NULL,
       走 IP key 配额; 第二次匿名 → 429
    5. **max_steps 熔断**: LLM 持续 tool_calls 不收, 主循环到 max_steps 强制
       break + 给 final, 不会把 chat_tool_calls 撑爆

策略
====
- 用 ``client`` fixture (集成测 conftest 已配好 schema/Redis/SMS/LLM mock)
- 走 ``fake_streaming_llm`` mock ``llm_client.astream_chat_with_meta``: 按调用
  次数返回不同 stream (FIFO 队列); 没脚本 → RuntimeError, 防主循环死循环
- 真打 PG (``ipo_service.get_ipo`` / ``persistence.*``); ``hybrid_search`` 在
  用例 1 mock 掉防依赖真 BGE embedding (不属于本 PR 测试目标)
- ``override_quota_settings`` monkey patch ``services.agent.quota.get_settings``
  让配额上限可控

复用 vs 内联 fixture 抉择
==========================
- BE-S2-007 / 008 的 ``_StreamScript`` / ``fake_streaming_llm`` / ``_parse_sse``
  都是**每个文件独立 inline** 的, 优点: 文件自洽, 改测试不用爬 conftest
- 这里也走同样的 inline 方式, 与现有惯例一致; 后续 Sprint 3 若再加 e2e 文件,
  可考虑统一抽到 ``tests/integration/_chat_helpers.py``
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters import llm_client
from app.adapters.llm_client import ChatStreamChunk, TokenUsage
from app.core.config import Settings, get_settings
from app.db.models.chat import (
    ChatMessage,
    ChatSession,
    ChatTokenUsage,
    ChatToolCall,
)
from app.db.models.ipo import IPO
from app.db.models.user import User
from app.security.jwt import create_access_token

# ─── fixture: 可编程 streaming LLM mock (与 BE-S2-007/008 同款, inline 自洽) ──


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

    - ``push(script)`` 入队一份 LLM 响应; 主循环每发起一次 ``astream_chat_with_meta``
      按 FIFO 弹出。没脚本时抛 ``RuntimeError`` 让测试快速失败 (而非主循环死循环).
    - ``captured_messages_per_call``: 每次调用入参 ``messages`` 的快照, 让测试
      可断言 history 注入 / system prompt 注入正确。
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
                "fake_streaming_llm: 测试未提供脚本但主循环又调了一次 LLM "
                f"(已调 {len(captured)} 次, 队列空)"
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
    yield push, captured


@pytest.fixture
async def llm_credential_envs(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """让 ``settings.has_llm_credential`` 为真; fake_streaming_llm 不真打远程,
    这里只是绕过主循环"无 key 提前 return"分支.
    """
    s = get_settings()
    monkeypatch.setattr(s, "siliconflow_api_key", "test-fake-key")
    yield


@pytest.fixture
def override_quota_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., Settings]:
    """覆盖 ``services.agent.quota.get_settings`` 让配额上限可控 (不动 lru_cache)."""
    base = get_settings()

    def _override(
        *,
        free_per_window: int | None = None,
        anon_per_window: int | None = None,
        vip_per_window: int | None = None,
        window_seconds: int | None = None,
        vip_user_id_whitelist: str | None = None,
    ) -> Settings:
        new = base.model_copy(
            update={
                k: v
                for k, v in {
                    "agent_quota_free_per_window": free_per_window,
                    "agent_quota_anonymous_per_window": anon_per_window,
                    "agent_quota_vip_per_window": vip_per_window,
                    "agent_quota_window_seconds": window_seconds,
                    "vip_user_id_whitelist": vip_user_id_whitelist,
                }.items()
                if v is not None
            }
        )
        monkeypatch.setattr("app.services.agent.quota.get_settings", lambda: new)
        return new

    return _override


# ─── helper: SSE 解析 + 用户 token ─────────────────────────────────────────


def _parse_sse(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """把 sse_starlette 的输出切成 (event, payload) list (与 BE-S2-007/008 同款)."""
    events: list[tuple[str, dict[str, Any]]] = []
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


async def _seed_user_and_token(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    phone_suffix: str = "9001",
) -> tuple[uuid.UUID, str]:
    """创建 1 条 User + 颁 access_token, 复用 BE-S2-008 集成测同款 helper."""
    async with session_factory() as s:
        u = User(
            phone=f"+8613800{phone_suffix}",
            invite_code=f"E2E{phone_suffix}",
        )
        s.add(u)
        await s.commit()
        await s.refresh(u)
        uid = u.user_id
    token, _ = create_access_token(user_id=uid)
    return uid, token


async def _seed_ipo(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    code: str = "0700.HK",
    name: str = "腾讯控股",
) -> None:
    """灌一条最小可用 IPO 入 ipos 表, 给 ``get_ipo_basic_info`` 真 DB 路径用."""
    async with session_factory() as s:
        s.add(
            IPO(
                code=code,
                name=name,
                market="HK",
                industry_l1="互联网",
                issue_price=Decimal("3.70"),
                issue_currency="HKD",
                pe_ratio=Decimal("15.5"),
                status="listed",
                data_source="e2e-fixture",
            )
        )
        await s.commit()


def _mock_hybrid_search_two_chunks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ipo_code: str = "0700.HK",
) -> list[Any]:
    """patch ``hybrid_search`` Tool 模块, 返回固定 2 条 chunk (避免依赖真 BGE).

    BE-S2-005 的 hybrid_search 在 BE-S2-005 集成测里已经有 PG 真打路径覆盖,
    这里 e2e 测的是"LangGraph 主循环 → tool dispatch → 引用源装配", 不重复
    走真 vector + RRF + reranker 链路.
    """
    from app.services.agent.tools import hybrid_search as hybrid_search_tool_mod
    from app.services.rag.hybrid_search import HybridSearchOutput, SearchResult

    fake_chunks = [
        SearchResult(
            chunk_id=uuid.uuid4(),
            doc_id="prospectus-0700",
            ipo_code=ipo_code,
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
            ipo_code=ipo_code,
            chunk_index=1,
            page=24,
            text="风险因素: 监管不确定性可能导致部分业务下线",
            score=0.81,
            rrf_score=0.029,
            vector_rank=3,
            bm25_rank=1,
        ),
    ]

    async def _fake_hybrid_search(
        session: Any, query: str, **kwargs: Any
    ) -> HybridSearchOutput:
        return HybridSearchOutput(
            results=fake_chunks,
            stats={
                "vector_hits": 50,
                "bm25_hits": 47,
                "fused_count": 78,
                "reranked": False,
                "elapsed_ms": 80,
            },
        )

    monkeypatch.setattr(
        hybrid_search_tool_mod, "hybrid_search", _fake_hybrid_search
    )
    return fake_chunks


# ─── 用例 1: 金线 happy path (注册 → 多 tool ReAct → 续聊) ────────────────


@pytest.mark.asyncio
async def test_e2e_register_diagnose_multitool_then_followup(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    fake_streaming_llm: tuple[
        Callable[[_StreamScript], None], list[list[dict[str, Any]]]
    ],
    llm_credential_envs: None,  # noqa: ARG001
) -> None:
    """金线 e2e: 注册用户 → seed IPO → ReAct 第 1 轮 (hybrid_search + basic_info)
    → 引用源装配 → 续聊带 session_id → history 注入 + DB 累积.

    覆盖关键链路:
    - chat_sessions.user_id 关联到注册用户
    - 多 tool 串联: chat_messages 6 条 (user / assistant-anchor / tool / assistant-anchor / tool / assistant-final)
    - chat_tool_calls 落 2 条 ok, chat_token_usage 落 ≥3 条 (3 步 LLM 调用)
    - 引用源装配后 SSE event=sources + chat_messages.citations 落表
    - 续聊: history_messages 注入到第 4 次 LLM 调用 messages
    - SSE 事件序列: start → tool_call ×2 → delta+ → sources → end
    """
    push, captured = fake_streaming_llm
    _, token = await _seed_user_and_token(session_factory, phone_suffix="9100")
    h = {"Authorization": f"Bearer {token}"}
    await _seed_ipo(session_factory, code="0700.HK", name="腾讯控股")
    _mock_hybrid_search_two_chunks(monkeypatch, ipo_code="0700.HK")

    # ─── 第 1 轮: 多 tool ReAct ──────────────────────────────────
    # 第 1 步 LLM: 决策调 hybrid_search
    push(
        _StreamScript(
            deltas=[],
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call_hs_001",
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
    # 第 2 步 LLM: 看到 hybrid_search 结果后, 再决策调 get_ipo_basic_info
    push(
        _StreamScript(
            deltas=[],
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call_basic_001",
                    "type": "function",
                    "function": {
                        "name": "get_ipo_basic_info",
                        "arguments": json.dumps({"code": "0700.HK"}),
                    },
                }
            ],
        )
    )
    # 第 3 步 LLM: 写 final, 引用 [1] [2]
    push(
        _StreamScript(
            deltas=[
                "腾讯控股 2024 Q3 营收同比 +15% [1], ",
                "存在监管不确定性 [2]. ",
                "PE 约 15.5, 估值合理.\n",
            ],
            finish_reason="stop",
        )
    )

    r1 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "诊断 0700.HK 营收和风险", "ipo_code": "0700.HK"},
        headers=h,
    )
    assert r1.status_code == 200, r1.text
    events1 = _parse_sse(r1.text)
    types1 = [e[0] for e in events1]

    # SSE 事件序列骨架
    assert types1[0] == "start"
    assert types1.count("tool_call") == 2
    assert "sources" in types1
    assert "delta" in types1
    assert types1[-1] == "end"
    assert "error" not in types1, f"金线不该报 error: {events1}"

    # tool_call 顺序符合 ReAct 决策
    tool_call_events = [e for e in events1 if e[0] == "tool_call"]
    assert tool_call_events[0][1]["name"] == "hybrid_search"
    assert tool_call_events[0][1]["status"] == "ok"
    assert tool_call_events[1][1]["name"] == "get_ipo_basic_info"
    assert tool_call_events[1][1]["status"] == "ok"

    # sources 装配的 citation
    sources = next(e for e in events1 if e[0] == "sources")
    assert len(sources[1]["citations"]) == 2
    assert sources[1]["citations"][0]["idx"] == 1
    assert sources[1]["citations"][0]["page"] == 12
    assert "营收" in sources[1]["citations"][0]["snippet"]

    # end 帧带 message_id + usage 聚合 + 无越界 [n]
    end1 = next(e for e in events1 if e[0] == "end")
    assert end1[1]["finish_reason"] == "stop"
    assert end1[1]["usage"]["prompt_tokens"] == 50 * 3, "3 步 LLM 调用聚合"
    assert end1[1]["invalid_citation_indices"] == []
    session_id = next(e for e in events1 if e[0] == "start")[1]["session_id"]

    # delta 拼回来含 [1][2]; disclaimer 兜底应该出现在 DB final 文本里
    delta_text1 = "".join(e[1]["content"] for e in events1 if e[0] == "delta")
    assert "[1]" in delta_text1
    assert "[2]" in delta_text1

    # ─── DB 落表验证 (第 1 轮) ───────────────────────────────────
    async with session_factory() as s:
        sessions = (await s.execute(select(ChatSession))).scalars().all()
        assert len(sessions) == 1
        assert str(sessions[0].session_id) == session_id
        assert sessions[0].ipo_code == "0700.HK"
        assert sessions[0].user_id is not None, "登录用户的 session.user_id 不应为空"

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
        # ReAct 多 tool: user → assistant(anchor1) → tool(hs)
        # → assistant(anchor2) → tool(basic) → assistant(final)
        assert roles == [
            "user",
            "assistant",  # anchor 1 (tool_call hybrid_search)
            "tool",
            "assistant",  # anchor 2 (tool_call basic_info)
            "tool",
            "assistant",  # final
        ], f"ReAct 多 tool 6 条 messages, 实际: {roles}"
        # final assistant 含 disclaimer + citations
        final = msgs[-1]
        assert "不构成投资建议" in final.content
        assert final.citations is not None
        assert len(final.citations) == 2

        # chat_tool_calls: 2 条 ok
        tcs = (
            (
                await s.execute(
                    select(ChatToolCall).order_by(ChatToolCall.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        assert len(tcs) == 2
        assert tcs[0].tool_name == "hybrid_search"
        assert tcs[0].status == "ok"
        assert tcs[1].tool_name == "get_ipo_basic_info"
        assert tcs[1].status == "ok"

        # token usage: 3 步 LLM 调用 → ≥3 条 (允许 final 之后 graph 不补; 至少 3)
        usages = (await s.execute(select(ChatTokenUsage))).scalars().all()
        assert len(usages) >= 3
        # 总 prompt_tokens = 50 * 3 = 150 (与 SSE end 一致)
        assert sum(u.input_tokens for u in usages) == 50 * 3

    # ─── 第 2 轮: 续聊 ───────────────────────────────────────────
    # 第 4 步 LLM: 直接 finish, 不调 tool
    push(
        _StreamScript(
            deltas=["补充: 行业空间充裕.\n"],
            finish_reason="stop",
        )
    )
    r2 = await client.post(
        "/api/v1/chat/diagnose",
        json={
            "question": "再补充一些行业空间",
            "ipo_code": "0700.HK",
            "session_id": session_id,
        },
        headers=h,
    )
    assert r2.status_code == 200, r2.text
    events2 = _parse_sse(r2.text)
    start2 = next(e for e in events2 if e[0] == "start")
    assert start2[1]["session_id"] == session_id, "续聊应共用同一 session"
    assert "tool_call" not in [e[0] for e in events2]

    # 第 4 次 LLM 入参 messages 应该带上一轮 history (≥1 条上一轮 user)
    assert len(captured) == 4, f"应共调 LLM 4 次, 实际 {len(captured)}"
    second_round_msgs = captured[3]
    user_in_history = [
        m for m in second_round_msgs if m.get("role") == "user"
    ]
    assert len(user_in_history) >= 2, (
        "续聊第 2 轮的 messages 至少含两条 user (上一轮 + 本轮): "
        f"{[m.get('role') for m in second_round_msgs]}"
    )
    # OpenAI 协议: tool role 不应回灌进续聊 messages (会触发 400)
    tool_in_history = [
        m for m in second_round_msgs if m.get("role") == "tool"
    ]
    assert tool_in_history == [], (
        "续聊不应把 tool role 回灌进 LLM messages "
        "(spec/04 §3.2 + persistence.session_history_to_messages 已 filter)"
    )

    # DB 累积验证
    async with session_factory() as s:
        sessions_after = (await s.execute(select(ChatSession))).scalars().all()
        assert len(sessions_after) == 1, "续聊不应新增 session"
        msgs_after = (
            (await s.execute(select(ChatMessage))).scalars().all()
        )
        assert len(msgs_after) == 6 + 2, (
            "续聊累计 8 条 message (第 1 轮 6 条 + 第 2 轮 user/assistant)"
        )


# ─── 用例 2: tool 失败沙盒兜底 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_diagnose_tool_failure_isolated_to_sse_tool_call_error(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    fake_streaming_llm: tuple[
        Callable[[_StreamScript], None], list[list[dict[str, Any]]]
    ],
    llm_credential_envs: None,  # noqa: ARG001
) -> None:
    """tool 内部抛 RuntimeError → 沙盒兜底 ToolResult.failure → SSE event=tool_call
    status='error' 透传, 但**不**冒 SSE event=error 整流中断; LLM 第二步看到
    tool error 仍能给 final 文本.

    覆盖 spec/04 §3.2 沙盒兜底 + spec/06 §法律隔离: tool 失败不能炸链路, 必须
    继续推进让 LLM 给"该 tool 暂不可用"的 fallback 答案.
    """
    push, _captured = fake_streaming_llm
    _, token = await _seed_user_and_token(session_factory, phone_suffix="9200")
    h = {"Authorization": f"Bearer {token}"}

    # mock ipo_service.get_ipo 抛异常.
    # basic_info.py 走 ``from app.services import ipo_service`` 拿模块引用, 所以 patch
    # 原模块的属性即可 (basic_info.ipo_service is app.services.ipo_service).
    # 这里走 dotted-string 路径让 mypy implicit-reexport 检查通过.
    async def _boom_get_ipo(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated DB connection drop")

    monkeypatch.setattr("app.services.ipo_service.get_ipo", _boom_get_ipo)

    # 第 1 步 LLM: 调 get_ipo_basic_info → 沙盒抛错 → ToolResult.failure
    push(
        _StreamScript(
            deltas=[],
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call_basic_fail",
                    "type": "function",
                    "function": {
                        "name": "get_ipo_basic_info",
                        "arguments": json.dumps({"code": "9999.UNKNOWN"}),
                    },
                }
            ],
        )
    )
    # 第 2 步 LLM: 看到 tool failure 后给 fallback 回答
    push(
        _StreamScript(
            deltas=[
                "暂时无法获取该公司基本面数据 (tool 不可用), ",
                "请稍后重试或联系运营.\n",
            ],
            finish_reason="stop",
        )
    )

    r = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "诊断 9999.UNKNOWN", "ipo_code": "9999.UNKNOWN"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    types = [e[0] for e in events]

    assert types[0] == "start"
    assert types[-1] == "end", f"终态应是 end (不是 error), 实际: {types}"
    # tool_call 透传, status=error
    tc_evt = next(e for e in events if e[0] == "tool_call")
    assert tc_evt[1]["name"] == "get_ipo_basic_info"
    assert tc_evt[1]["status"] == "error", (
        f"tool 沙盒抛错应翻译成 SSE tool_call status=error, 实际 {tc_evt[1]}"
    )
    assert tc_evt[1]["error"] is not None
    # **关键断言**: 不应冒成顶层 error event (沙盒兜底有效)
    assert "error" not in types, (
        f"tool 失败不应外冒成 SSE error 帧, 全 events: {types}"
    )

    # final assistant 仍写 DB
    async with session_factory() as s:
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
        assert roles[-1] == "assistant"
        assert "暂时无法获取" in msgs[-1].content
        assert "不构成投资建议" in msgs[-1].content

        tcs = (await s.execute(select(ChatToolCall))).scalars().all()
        assert len(tcs) == 1
        assert tcs[0].status == "error"
        assert tcs[0].error_message is not None and (
            "RuntimeError" in tcs[0].error_message
            or "simulated" in tcs[0].error_message
        )


# ─── 用例 3: forbidden_pattern_filter 端到端 (DB 落库版本被替换) ──────────


@pytest.mark.asyncio
async def test_e2e_diagnose_forbidden_pattern_replaced_in_db_final(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_streaming_llm: tuple[
        Callable[[_StreamScript], None], list[list[dict[str, Any]]]
    ],
    llm_credential_envs: None,  # noqa: ARG001
) -> None:
    """LLM 输出含违规词 (强烈推荐 / 必涨 等), 主循环 ``forbidden_pattern_filter``
    会替换为 ``[已合规过滤]`` 后写 chat_messages.content. 这是 spec/04 §3.3 §B
    "中立性合规" 的端到端保障; 即便 LLM prompt 写得不够严, 输出层也能挡掉.

    SSE delta 流的设计: 主循环是先 yield 原始 delta 给端层 (流式体验), 之后
    才整段过 forbidden_filter 写 DB; 因此 SSE delta 文本可能含违规词, 但 **DB
    final assistant.content 必含 [已合规过滤] 替代**, 这是用户最终复盘看到的
    版本 (FE 在 end 帧后会拉 message_id 拿权威版本).
    """
    push, _captured = fake_streaming_llm
    _, token = await _seed_user_and_token(session_factory, phone_suffix="9300")
    h = {"Authorization": f"Bearer {token}"}

    # LLM 一次性给含违规词的 final, 不带 disclaimer (验 ensure_disclaimer 兜底)
    push(
        _StreamScript(
            deltas=[
                "公司基本面优秀, ",
                "强烈推荐买入, ",
                "未来必涨!",
            ],
            finish_reason="stop",
        )
    )

    r = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "测试合规护栏"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    assert events[0][0] == "start"
    assert events[-1][0] == "end"

    # DB final: 违规词被替换 + disclaimer 自动追加
    async with session_factory() as s:
        msgs = (
            (
                await s.execute(
                    select(ChatMessage)
                    .where(ChatMessage.role == "assistant")
                    .order_by(ChatMessage.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        assert len(msgs) == 1
        final_text = msgs[0].content
        assert "[已合规过滤]" in final_text, (
            f"违规词应被 forbidden_pattern_filter 替换为 [已合规过滤]; "
            f"实际 final: {final_text!r}"
        )
        # 原文里的违规词应已被剥除
        assert "强烈推荐买入" not in final_text
        assert "必涨" not in final_text
        # 中性内容保留
        assert "公司基本面优秀" in final_text
        # disclaimer 兜底
        assert "不构成投资建议" in final_text


# ─── 用例 4: 匿名 e2e + IP 配额 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_anonymous_diagnose_then_ip_quota_429(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_streaming_llm: tuple[
        Callable[[_StreamScript], None], list[list[dict[str, Any]]]
    ],
    llm_credential_envs: None,  # noqa: ARG001
    override_quota_settings: Callable[..., Settings],
) -> None:
    """匿名 e2e: 不带 token → 第 1 次走完 SSE + chat_sessions.user_id IS NULL +
    record_usage 真扣 IP 配额; 第 2 次同 IP → 429 + ChatQuotaExceededResponse.

    覆盖 spec/04 §1.3 "匿名也能用 AI" + BE-S2-008 IP 限流分支 (与 BE-S2-008
    单点测互补: 那条只测 quota 模块行为; 这里测端到端 chat_sessions 落表 +
    user_id 留空 + 多轮共享 IP key).
    """
    override_quota_settings(anon_per_window=1, window_seconds=86400)
    push, _captured = fake_streaming_llm
    push(_StreamScript(deltas=["匿名第一次答复\n"], finish_reason="stop"))

    # 第 1 次 (匿名): 200 + SSE 走完
    r1 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "匿名问问"},
    )
    assert r1.status_code == 200, r1.text
    events1 = _parse_sse(r1.text)
    assert events1[0][0] == "start"
    assert events1[-1][0] == "end"
    delta_text = "".join(e[1]["content"] for e in events1 if e[0] == "delta")
    assert "匿名第一次答复" in delta_text

    async with session_factory() as s:
        sessions = (await s.execute(select(ChatSession))).scalars().all()
        assert len(sessions) == 1
        assert sessions[0].user_id is None, (
            "匿名调用时 chat_sessions.user_id 应为 NULL"
        )
        # final assistant 仍写 DB (匿名也走完整 audit log)
        msgs = (
            (await s.execute(select(ChatMessage))).scalars().all()
        )
        assert {m.role for m in msgs} == {"user", "assistant"}

    # 第 2 次 (同 IP, ASGITransport 默认 client.host=testclient): 429
    r2 = await client.post(
        "/api/v1/chat/diagnose",
        json={"question": "再来一次"},
    )
    assert r2.status_code == 429, r2.text
    detail = r2.json()["detail"]
    assert detail["code"] == "agent_quota_exceeded"
    assert detail["quota"]["plan"] == "anonymous"
    assert detail["quota"]["limit"] == 1
    assert detail["quota"]["used"] == 1
    assert r2.headers.get("retry-after") is not None

    # 第 2 次的 user_message 不应落 DB (HTTPException 在进流前)
    async with session_factory() as s:
        msgs_after = (
            (await s.execute(select(ChatMessage))).scalars().all()
        )
        assert len(msgs_after) == 2, (
            "429 不应再写 user_message; 仍只有第 1 轮的 user/assistant 两条"
        )


# ─── 用例 5: max_steps 熔断 (LLM 一直 tool_calls 不收) ─────────────────


@pytest.mark.asyncio
async def test_e2e_diagnose_max_steps_circuit_breaker_forces_final(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_streaming_llm: tuple[
        Callable[[_StreamScript], None], list[list[dict[str, Any]]]
    ],
    llm_credential_envs: None,  # noqa: ARG001
) -> None:
    """LLM 持续 tool_calls 不 finish, 主循环到 max_steps 强制 break.

    设计: 最后一步 (``is_last_step``) 主循环不再传 tools_schema, 强制 LLM 给
    文本答案; 这是 spec/04 §3.2 "熔断" 兜底, 防 LLM 拿着 tool 死循环, 烧 token
    / 撑爆 chat_tool_calls 表.

    复现: max_steps=2, push 2 个脚本:
    - 第 1 步: tool_calls=[get_ipo_basic_info] (会被执行)
    - 第 2 步 (最后一步): is_last_step=True 时 tools=None → LLM 必须给文本.
      这里仍 push 一个 stop 脚本提供 final 文本
    验证: chat_tool_calls 落 1 条 (第 2 步不调 tool), final assistant 写 DB.
    """
    push, _captured = fake_streaming_llm
    _, token = await _seed_user_and_token(session_factory, phone_suffix="9400")
    h = {"Authorization": f"Bearer {token}"}
    await _seed_ipo(session_factory, code="0001.HK", name="测试一只")

    # 第 1 步: 决策调 tool
    push(
        _StreamScript(
            deltas=[],
            finish_reason="tool_calls",
            tool_calls=[
                {
                    "id": "call_step1",
                    "type": "function",
                    "function": {
                        "name": "get_ipo_basic_info",
                        "arguments": json.dumps({"code": "0001.HK"}),
                    },
                }
            ],
        )
    )
    # 第 2 步 (max_steps=2): 主循环不传 tools, LLM 给文本收尾
    push(
        _StreamScript(
            deltas=["综合上述基本面, 暂无明显短期催化.\n"],
            finish_reason="stop",
        )
    )

    r = await client.post(
        "/api/v1/chat/diagnose",
        json={
            "question": "诊断 0001.HK",
            "ipo_code": "0001.HK",
            "max_steps": 2,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    types = [e[0] for e in events]

    assert types[0] == "start"
    assert types[-1] == "end"
    assert types.count("tool_call") == 1, (
        f"max_steps=2 应只调 1 次 tool, 实际 events: {types}"
    )
    delta_text = "".join(e[1]["content"] for e in events if e[0] == "delta")
    assert "综合上述基本面" in delta_text

    async with session_factory() as s:
        tcs = (await s.execute(select(ChatToolCall))).scalars().all()
        assert len(tcs) == 1
        assert tcs[0].tool_name == "get_ipo_basic_info"
        assert tcs[0].status == "ok"

        msgs = (
            (
                await s.execute(
                    select(ChatMessage).order_by(ChatMessage.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        # ReAct: user → assistant(anchor) → tool → assistant(final)
        roles = [m.role for m in msgs]
        assert roles == ["user", "assistant", "tool", "assistant"], roles
        assert "不构成投资建议" in msgs[-1].content
