"""ReAct 主循环 (BE-S2-007 Tool Use 第 4 层).

实现 spec/04 §3.2 的 ReAct (Reason → Act → Reflect) 步进, 把 BE-S2-002 LLM
facade + BE-S2-006a Tool 注册中心 + BE-S2-006b 全部 Tool + BE-S2-005
hybrid_search 串成 "user_query → 多轮工具调用 → 最终带引用的回答".

为什么不上 LangGraph
====================
- spec 写"LangGraph 主循环"是技术方案描述; 我们已有 ToolRegistry / Sandbox /
  LLM facade, 一个 ~250 行的纯 async 循环就把 ReAct 跑起来, 不引入 LangGraph
  300+ MB 的依赖
- LangGraph StateGraph 抽象对当前需求 (固定 3 节点 plan/act/reflect, 顺序串行)
  略 overkill; 真有"复杂条件分支 / human-in-the-loop"再上 LangGraph
- 文件命名仍叫 ``graph.py`` 与 spec 对齐; 后续若换 LangGraph 实现只换 ``run``
  内部, 端层 / 单测 API 不动

主流程 (run)
============
::

    plan: LLM(messages, tools=tools, stream=True) → 拿 (delta_stream, finish_reason, tool_calls)
       ├─ 没 tool_calls: yield LLM delta (流式), reflect 节点收尾
       └─ 有 tool_calls: act 节点 dispatch
            for each tool_call:
              INSERT chat_tool_calls pending
              tool.runner(raw_args, **deps)
              UPDATE chat_tool_calls 终态
              yield ToolCallEvent
            把 tool 结果回灌 messages, 进入下一步 plan

    最多 ``max_steps`` 步. 终止条件:
    - LLM 返回 stop / length 等非 tool_calls
    - 步数超限 (强制收尾, 让 LLM 用现有信息再生成一次最终回答)

事件协议
========
``AgentEvent`` 是 frozen dataclass union, 端层 ``api/v1/chat.py`` 接收事件后
翻译成 SSE; 单测可直接断言事件序列.

依赖注入 (deps)
================
- ``session``: AsyncSession, 给 ``hybrid_search`` Tool 复用主循环事务
- 其他 tools 自己 ``get_session_factory()`` 起独立短事务 (BE-S2-006b 已落地)

不在本文件做
============
- SSE 编码 / 端层 disclaimer 兜底 (api/v1/chat.py)
- 配额检查 (BE-S2-008 给端层)
- 评测 / bad case (BE-S2-009)
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import llm_client
from app.adapters.llm_client import (
    ChatStreamChunk,
    LLMConfigError,
    LLMError,
    TokenUsage,
    ensure_disclaimer,
    forbidden_pattern_filter,
)
from app.core.config import get_settings
from app.core.logging import logger
from app.services.agent import persistence
from app.services.agent.citation import CitationBundle, assemble
from app.services.agent.tool_registry import (
    Tool,
    ToolResult,
    list_openai_schemas,
)
from app.services.agent.tool_registry import (
    get as get_tool,
)

# ─── 事件协议 ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StartedEvent:
    """主循环启动 (端层翻 SSE event=start)."""

    chat_session_id: uuid.UUID
    model: str
    ipo_code: str | None


@dataclass(frozen=True, slots=True)
class TokenDeltaEvent:
    """LLM 流式增量 (端层翻 SSE event=delta)."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    """单个 tool 调用结束 (端层翻 SSE event=tool_call)."""

    name: str
    args: dict[str, Any] | None
    status: Literal["ok", "error", "timeout"]
    latency_ms: int
    error: str | None = None
    result_preview: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class FinalAnswerEvent:
    """最终回答装配完成 (端层翻 SSE event=sources + 写 chat_messages.citations).

    text 里 disclaimer / forbidden_filter / citation 校验已全做完, 端层不再二次清洗.
    """

    message_id: uuid.UUID
    text: str
    citation_bundle: CitationBundle
    usage_aggregate: TokenUsage
    finish_reason: str
    invalid_citation_indices: list[int]


@dataclass(frozen=True, slots=True)
class StepErrorEvent:
    """主循环捕获到 LLM 配置 / 网络 / 上游异常, 端层翻 SSE event=error 后断流."""

    message: str
    cause_type: str = ""


AgentEvent = (
    StartedEvent
    | TokenDeltaEvent
    | ToolCallEvent
    | FinalAnswerEvent
    | StepErrorEvent
)


# ─── 内部工具 ──────────────────────────────────────────────────────────────


def _result_preview(data: dict[str, Any] | None, max_keys: int = 8) -> dict[str, Any] | None:
    """对 ToolResult.data 做轻量摘要给端层 SSE; 防 hybrid_search 整张表 dump."""
    if not data:
        return None
    preview: dict[str, Any] = {}
    for i, (k, v) in enumerate(data.items()):
        if i >= max_keys:
            preview["__truncated__"] = True
            break
        if isinstance(v, list):
            preview[k] = f"<list len={len(v)}>"
        elif isinstance(v, dict):
            preview[k] = f"<dict keys={len(v)}>"
        elif isinstance(v, str) and len(v) > 200:
            preview[k] = v[:200] + "…"
        else:
            preview[k] = v
    return preview


def _serialize_tool_result_for_llm(result: ToolResult) -> str:
    """tool_result → role=tool message.content (LLM 协议要求 string)."""
    if result.ok:
        # data 为空 dict 也能 dumps; LLM 看到 {} 自己会判断
        try:
            return json.dumps(result.data or {}, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return json.dumps({"error": "result not JSON-serializable"}, ensure_ascii=False)
    return json.dumps(
        {"error": result.error or "unknown tool error"},
        ensure_ascii=False,
    )


def _aggregate_usage(usages: list[TokenUsage]) -> TokenUsage:
    """聚合多次 LLM 调用的 usage (主循环每步一次)."""
    if not usages:
        return TokenUsage.empty()
    total_p = sum(u.prompt_tokens for u in usages)
    total_c = sum(u.completion_tokens for u in usages)
    total_t = sum(u.total_tokens for u in usages)
    total_cost = sum((u.cost_cny for u in usages), start=usages[0].cost_cny.__class__(0))
    return TokenUsage(total_p, total_c, total_t, total_cost)


def _resolve_provider(model: str) -> str:
    """从 model 路由名抽 provider (用来落 chat_token_usage.provider)."""
    if model.startswith("openai/"):
        return "siliconflow"
    if model.startswith("deepseek/"):
        return "deepseek"
    if model.startswith("zhipu/"):
        return "zhipu"
    return "siliconflow"


# ─── 一次 LLM 调用 (流式 + 累积 tool_calls + 累积 delta) ────────────────────


async def _call_llm_streaming(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None,
    model: str,
    temperature: float,
    max_tokens: int,
) -> tuple[AsyncIterator[ChatStreamChunk], None]:
    """thin wrapper around ``llm_client.astream_chat_with_meta``, 仅做日志.

    返回 (async_iter, None) 让调用方 ``async for`` 消费; tuple 形保留扩展位.
    """
    logger.info(
        f"agent.graph.llm_call model={model} msgs={len(messages)} "
        f"tools={len(tools) if tools else 0} temp={temperature}"
    )
    return llm_client.astream_chat_with_meta(
        messages,
        model=model,
        tools=tools,
        temperature=temperature,
        max_tokens=max_tokens,
    ), None


# ─── tool dispatch ─────────────────────────────────────────────────────────


async def _invoke_tool(
    tool_call: dict[str, Any],
    *,
    session: AsyncSession,
) -> tuple[str, dict[str, Any] | None, ToolResult]:
    """解析单个 OpenAI tool_call, 走 registry.runner. 不 raise.

    返回 ``(tool_name, raw_args_dict_or_None, ToolResult)``.
    raw_args 解析失败时为 None, ToolResult 已是 failure (不抛, sandbox 已兜底).

    特殊处理: ``hybrid_search`` Tool 注入 ``session=session`` 让它复用主循环
    事务 (BE-S2-006b 设计). 其他 Tool 走自己的 get_session_factory 短事务.
    """
    fn = tool_call.get("function") or {}
    tool_name = str(fn.get("name", ""))
    raw_args_str = fn.get("arguments") or "{}"

    if not tool_name:
        return ("", None, ToolResult.failure("tool_call 缺少 function.name"))

    try:
        raw_args = json.loads(raw_args_str) if isinstance(raw_args_str, str) else raw_args_str
        if not isinstance(raw_args, dict):
            raise ValueError("arguments 不是 JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(
            f"agent.graph.tool_args_parse_fail name={tool_name} err={e} raw={raw_args_str!r}"
        )
        return (tool_name, None, ToolResult.failure(f"参数解析失败: {e}"))

    tool: Tool | None = get_tool(tool_name)
    if tool is None:
        logger.warning(f"agent.graph.tool_not_found name={tool_name}")
        return (
            tool_name,
            raw_args,
            ToolResult.failure(f"未注册的 tool: {tool_name}"),
        )

    # 按 tool 名注入 deps. 仅 hybrid_search 需要主循环 session
    deps: dict[str, Any] = {}
    if tool_name == "hybrid_search":
        deps["session"] = session

    try:
        result = await tool.runner(raw_args, **deps)
    except Exception as e:  # 沙盒应该已经吞了, 这里是双保险
        logger.exception(
            f"agent.graph.tool_runner_uncaught name={tool_name}: {e}"
        )
        return (tool_name, raw_args, ToolResult.failure(f"runner 内部错误: {e.__class__.__name__}"))

    return (tool_name, raw_args, result)


async def _dispatch_tool_calls(
    tool_calls: list[dict[str, Any]],
    *,
    session: AsyncSession,
    assistant_message_id: uuid.UUID,
    chat_session_id: uuid.UUID,
    max_parallel: int,
) -> tuple[
    list[tuple[dict[str, Any], ToolCallEvent]],
    list[dict[str, Any]],  # role=tool messages 数组 (回灌 LLM 用)
    list[dict[str, Any]],  # hybrid_search 累积 chunks
]:
    """串行执行 tool_calls (并行风险大, 单进程 DB 连接限并不划算).

    返回:
    1. ``(raw_tool_call, ToolCallEvent)`` 序列, 端层 yield 用
    2. role=tool messages, 回灌 LLM
    3. hybrid_search 累积 chunks (citation 装配用)
    """
    # 截断保护
    if len(tool_calls) > max_parallel:
        logger.warning(
            f"agent.graph.tool_calls_truncated requested={len(tool_calls)} "
            f"cap={max_parallel}"
        )
        tool_calls = tool_calls[:max_parallel]

    events: list[tuple[dict[str, Any], ToolCallEvent]] = []
    tool_role_messages: list[dict[str, Any]] = []
    hybrid_chunks: list[dict[str, Any]] = []

    for tc in tool_calls:
        openai_call_id = str(tc.get("id") or f"call_{uuid.uuid4().hex[:12]}")
        fn = tc.get("function") or {}
        tool_name = str(fn.get("name", ""))

        # 1. INSERT pending (在 invoke 之前, 拿到 DB tool_call_id; latency 含 invoke 全过程)
        record = await persistence.insert_tool_call_pending(
            session,
            message_id=assistant_message_id,
            tool_name=tool_name,
            args=None,  # 真正的 args 在 invoke 解析后再 update; 暂为 None
        )

        t0 = time.monotonic()
        name, args, result = await _invoke_tool(tc, session=session)
        latency_ms = int((time.monotonic() - t0) * 1000)

        # 2. UPDATE 终态; 沙盒返回的 elapsed_ms 比这里更精确 (它不含 invoke 解析开销)
        if result.elapsed_ms > 0:
            latency_ms = result.elapsed_ms
        status: Literal["ok", "error", "timeout"]
        if result.ok:
            status = "ok"
        elif result.error and "超时" in result.error:
            status = "timeout"
        else:
            status = "error"

        record.args = args
        await persistence.finalize_tool_call(
            session,
            record=record,
            status=status,
            result=result.data if result.ok else None,
            error_message=result.error,
            latency_ms=latency_ms,
        )

        # 3. 记 role=tool message (LLM 协议要求每个 tool_call 配一条 tool message)
        tool_msg_content = _serialize_tool_result_for_llm(result)
        await persistence.insert_tool_role_message(
            session,
            chat_session_id=chat_session_id,
            openai_tool_call_id=openai_call_id,
            content=tool_msg_content,
        )
        tool_role_messages.append(
            {
                "role": "tool",
                "tool_call_id": openai_call_id,
                "content": tool_msg_content,
            }
        )

        # 4. 收集 hybrid_search chunks 给 citation 装配
        if name == "hybrid_search" and result.ok and result.data:
            results = result.data.get("results")
            if isinstance(results, list):
                hybrid_chunks.extend(
                    r for r in results if isinstance(r, dict)
                )

        # 5. 事件
        events.append(
            (
                tc,
                ToolCallEvent(
                    name=name,
                    args=args,
                    status=status,
                    latency_ms=latency_ms,
                    error=result.error if not result.ok else None,
                    result_preview=_result_preview(result.data) if result.ok else None,
                ),
            )
        )

    return events, tool_role_messages, hybrid_chunks


# ─── 主循环 ────────────────────────────────────────────────────────────────


async def run(
    *,
    session: AsyncSession,
    chat_session_id: uuid.UUID,
    user_message_id: uuid.UUID,
    user_query: str,
    system_prompt: str,
    history_messages: list[dict[str, Any]] | None = None,
    model: str | None = None,
    max_steps: int | None = None,
    ipo_code: str | None = None,
) -> AsyncIterator[AgentEvent]:
    """ReAct 主循环入口. 端层 ``api/v1/chat.py`` 直接 ``async for`` 消费.

    职责
    ----
    - 跑 plan → act → reflect 步进, 直到 LLM 给出非 tool_calls 回答 / 步数耗尽
    - 把所有 LLM 调用的 token usage 落 ``chat_token_usage`` (一行一次 LLM 调用)
    - 把所有 tool 调用落 ``chat_tool_calls`` (pending → 终态)
    - 写 ``role=assistant`` 最终 message (含 citations) 到 ``chat_messages``
    - yield AgentEvent 事件流给端层

    参数
    ----
    - ``session``: AsyncSession (端层管事务: 主循环只 add/flush, end 时端层 commit)
    - ``chat_session_id``: 已存在的 ChatSession PK (端层先 ``get_or_create_session``)
    - ``user_message_id``: 已 INSERT 的 user ChatMessage PK
    - ``user_query``: 用户原文 (system / history 之外的 last user message)
    - ``system_prompt``: 已通过 ``system_prompt.build_system_prompt(...)`` 生成
    - ``history_messages``: OpenAI messages 协议的历史 (不含本轮 user / system),
      用于续聊
    - ``model``: 可选, 走 settings.llm_primary_model 默认
    - ``max_steps``: 可选, 走 settings.agent_max_steps 默认; 上限 10

    yield
    -----
    StartedEvent → (LLMTokenDelta | ToolCallEvent) ×N → FinalAnswerEvent
    上游异常时直接 StepErrorEvent 收尾.
    """
    settings = get_settings()
    use_model = model or settings.llm_primary_model
    use_max_steps = min(max_steps or settings.agent_max_steps, 10)
    decision_temp = settings.agent_decision_temperature
    final_temp = settings.llm_chat_default_temperature
    max_tokens_step = settings.agent_max_tokens_per_step
    max_parallel_tools = settings.agent_max_tool_calls_per_step

    yield StartedEvent(
        chat_session_id=chat_session_id, model=use_model, ipo_code=ipo_code
    )

    if not settings.has_llm_credential:
        # 没配 LLM 直接给端层一个明确的引导, 不要等到流式才发现
        yield StepErrorEvent(
            message=(
                "后端未配置 LLM API Key (SILICONFLOW_API_KEY / DEEPSEEK_API_KEY 任一)"
            ),
            cause_type="LLMConfigError",
        )
        return

    # 拼基础 messages: system + history + 本轮 user
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    if history_messages:
        messages.extend(history_messages)
    messages.append({"role": "user", "content": user_query})

    tools_schema = list_openai_schemas() or None
    usages: list[TokenUsage] = []
    hybrid_chunks_acc: list[dict[str, Any]] = []
    final_text_buffer: list[str] = []
    final_finish_reason: str = "stop"
    final_message_id: uuid.UUID | None = None

    for step in range(1, use_max_steps + 1):
        is_last_step = step == use_max_steps
        # 决策步: temperature=0; 工具不可用时 (最后一步强制收尾) 不传 tools
        # 让 LLM 必须给文本答案
        step_tools = None if is_last_step else tools_schema
        step_temp = decision_temp if not is_last_step else final_temp

        try:
            stream_iter, _ = await _call_llm_streaming(
                messages,
                tools=step_tools,
                model=use_model,
                temperature=step_temp,
                max_tokens=max_tokens_step,
            )
        except LLMConfigError as e:
            yield StepErrorEvent(message=str(e), cause_type="LLMConfigError")
            return
        except LLMError as e:
            yield StepErrorEvent(message=str(e), cause_type=e.__class__.__name__)
            return

        # 消费 stream: 累积 delta_buffer, 不立即 yield (要等 finish_reason 才知道
        # 这步是中间思考还是最终回答). step 结束后:
        # - 最终步 (finish_reason != tool_calls): 回放 delta_buffer 给端层
        # - 中间步 (有 tool_calls): 丢弃 delta_buffer 内容作为 LLM "思考"
        delta_buffer: list[str] = []
        step_finish_reason: str | None = None
        step_usage: TokenUsage | None = None
        step_tool_calls: list[dict[str, Any]] | None = None

        try:
            async for chunk in stream_iter:
                if chunk.delta:
                    delta_buffer.append(chunk.delta)
                if chunk.finish_reason is not None:
                    step_finish_reason = chunk.finish_reason
                    step_usage = chunk.usage
                    step_tool_calls = chunk.tool_calls
        except LLMError as e:
            yield StepErrorEvent(message=str(e), cause_type=e.__class__.__name__)
            return
        except Exception as e:
            yield StepErrorEvent(
                message=f"LLM 流式异常: {e.__class__.__name__}: {e}",
                cause_type=e.__class__.__name__,
            )
            return

        # 步内 LLM 调用收尾: INSERT chat_token_usage. message_id 在 user_message
        # 上 (Sprint 2 接受这个口径; final assistant 还没 INSERT, 等 reflect 后写).
        if step_usage is not None:
            usages.append(step_usage)
            try:
                await persistence.insert_token_usage(
                    session,
                    message_id=user_message_id,
                    usage=step_usage,
                    model=use_model,
                    provider=_resolve_provider(use_model),
                )
            except Exception as e:
                # 落 token usage 不应让主循环挂; 记日志继续
                logger.error(f"agent.graph.token_usage_insert_fail step={step}: {e}")

        # 决策: 还要继续 tool 调用吗?
        if step_tool_calls and not is_last_step:
            logger.info(
                f"agent.graph.act step={step} tool_calls={len(step_tool_calls)}"
            )
            # 此时 delta_buffer 一般为空 (tool_choice=auto 流式时 content 不出);
            # 即便非空也作为"中间思考"丢弃, 不入 final
            assistant_text = "".join(delta_buffer).strip()

            # 先 INSERT 一个临时 assistant message 作 tool_calls 的 anchor (注意:
            # OpenAI 协议要求 messages[] 里 tool_calls 之前必须有 role=assistant
            # message). 对话历史回放兼容靠这里
            anchor_msg = await persistence.insert_assistant_message(
                session,
                chat_session_id=chat_session_id,
                content=assistant_text,
                citations=None,
            )

            # 把 assistant.tool_calls 加到 LLM messages (协议要求)
            messages.append(
                {
                    "role": "assistant",
                    "content": assistant_text or None,
                    "tool_calls": step_tool_calls,
                }
            )

            events, tool_role_messages, new_chunks = await _dispatch_tool_calls(
                step_tool_calls,
                session=session,
                assistant_message_id=anchor_msg.message_id,
                chat_session_id=chat_session_id,
                max_parallel=max_parallel_tools,
            )
            for _, evt in events:
                yield evt
            messages.extend(tool_role_messages)
            hybrid_chunks_acc.extend(new_chunks)
            # 进入下一步 plan
            continue

        # 没 tool_calls (或最后一步强制收尾): final answer
        # 把累积的 delta 回放给端层 SSE. 一次性 yield (不再流式分批) 也能让前端
        # 拿到最终文本; latency = LLM 单步生成完成时间, 体验上仍是"流式"
        for d in delta_buffer:
            if d:
                yield TokenDeltaEvent(text=d)
        final_text_buffer = delta_buffer
        final_finish_reason = step_finish_reason or "stop"
        break

    # 主循环结束: 装配 citation + disclaimer + forbidden filter + INSERT assistant
    raw_text = "".join(final_text_buffer).strip()

    # spec/04 §3.3 §B forbidden_pattern_filter (中立性合规)
    cleaned, hits = forbidden_pattern_filter(raw_text)
    if hits:
        logger.warning(
            f"agent.graph.forbidden_hits count={len(hits)} hits={hits[:5]}"
        )

    # citation pipeline
    bundle = assemble(
        hybrid_search_results=hybrid_chunks_acc, answer_text=cleaned
    )

    # disclaimer 兜底 (主循环统一 append; 端层不再二次 append)
    final_text = ensure_disclaimer(bundle.validated_text)

    # 写 assistant message (最终)
    citations_payload = [c.to_dict() for c in bundle.citations] or None
    final_msg = await persistence.insert_assistant_message(
        session,
        chat_session_id=chat_session_id,
        content=final_text,
        citations=citations_payload,
    )
    final_message_id = final_msg.message_id

    aggregate = _aggregate_usage(usages)
    yield FinalAnswerEvent(
        message_id=final_message_id,
        text=final_text,
        citation_bundle=bundle,
        usage_aggregate=aggregate,
        finish_reason=final_finish_reason,
        invalid_citation_indices=list(bundle.invalid_citation_indices),
    )


__all__ = [
    "AgentEvent",
    "FinalAnswerEvent",
    "StartedEvent",
    "StepErrorEvent",
    "TokenDeltaEvent",
    "ToolCallEvent",
    "run",
]
