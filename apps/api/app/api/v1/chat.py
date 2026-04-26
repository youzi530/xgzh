"""Chat / Agent SSE 端层 (BE-S2-007).

POST ``/api/v1/chat/diagnose`` — ReAct 主循环 SSE 入口.
GET  ``/api/v1/chat/sessions`` — (Sprint 3 占位, 当前只放 chat 路径)

设计取舍
========
- **匿名友好**: 走 ``get_optional_user``; 没登录也能跑 (BE-S2-008 之后再叠"匿名 5 次/天" 限流)
- **会话事务边界**: 端层 ``async with factory() as session`` 起独立 session; 主循环
  只写不 commit, 端层在 SSE end 之前 commit (中间 tool 失败仍保留 audit log).
  错误时 end 后才 rollback (避免 SSE 已 yield 但事务回滚的悖论 — 这里折衷:
  错误事件落表是必要的, 主循环已 logger 记录, DB 持久化失败不阻塞流)
- **DISCLAIMER 兜底位置**: 已在 ``graph.run`` 内 ``ensure_disclaimer`` 处理一次;
  端层不重复 append, 但保留 forbidden_pattern_filter 的"误漏"双保险
- **错误事件 + end 都发**: SSE 客户端约定: ``event=error`` 表示中途异常, 之后
  仍发一个 ``event=end`` 让前端 EventSource ``onmessage`` 走 close 流程
- **不在端层做配额**: BE-S2-008 加 quota dependency, 当前仅记录, 不限制

事件协议
========
- event=start    {session_id, ipo_code, model}
- event=delta    {content}                            (LLM token 增量)
- event=tool_call {name, args, status, latency_ms, error?, result_preview?}
- event=sources  {citations: [...]}                   (LLM 写完后一次性下发)
- event=end      {message_id, finish_reason, usage, invalid_citation_indices}
- event=error    {message}                            (主循环异常 / 上游崩)
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.logging import logger
from app.db.base import get_session
from app.db.models.user import User
from app.schemas.chat import (
    ChatCitation,
    ChatDiagnoseRequest,
    ChatEndPayload,
    ChatErrorPayload,
    ChatSourcesPayload,
    ChatStartPayload,
    ChatTokenUsageDTO,
    ChatToolCallPayload,
)
from app.security import get_optional_user
from app.services.agent import graph as agent_graph
from app.services.agent import persistence

# 触发 6 个 Tool side-effect 注册 (没 tools 主循环没东西可调)
from app.services.agent import tools as _tools_pkg  # noqa: F401  (intentional side effect)
from app.services.agent.system_prompt import build_system_prompt

router = APIRouter(prefix="/chat", tags=["chat"])


def _sse(event_type: str, payload: dict[str, Any]) -> dict[str, str]:
    """SSE 协议: ``{"event": ..., "data": json}``; 中文不转义."""
    return {"event": event_type, "data": json.dumps(payload, ensure_ascii=False)}


def _initial_title_from_query(query: str) -> str:
    """新会话首条 user query 截前 32 字做 title (Sprint 3 用 LLM 抽)."""
    s = query.strip().splitlines()[0] if query else ""
    return s[:32] if s else "新对话"


@router.post("/diagnose")
async def chat_diagnose(
    req: ChatDiagnoseRequest,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_optional_user),
) -> EventSourceResponse:
    """流式 Agent 诊断: 用户提交问题 → ReAct 主循环 → SSE 增量 + 工具透传 + 引用源.

    匿名 / 登录都能调; BE-S2-008 之后会接配额.
    """
    user_id: uuid.UUID | None = user.user_id if user is not None else None
    initial_title = _initial_title_from_query(req.question)

    async def generator() -> AsyncIterator[dict[str, str]]:
        # 1. session / user message 持久化 (失败 → 直接 error end)
        try:
            chat_session = await persistence.get_or_create_session(
                session,
                session_id=req.session_id,
                user_id=user_id,
                ipo_code=req.ipo_code,
                initial_title=initial_title,
            )
            history = (
                await persistence.list_session_messages(
                    session, chat_session_id=chat_session.session_id, limit=50
                )
                if req.session_id is not None
                else []
            )
            history_messages = persistence.session_history_to_messages(history)
            user_msg = await persistence.insert_user_message(
                session,
                chat_session_id=chat_session.session_id,
                content=req.question,
            )
        except Exception as e:
            logger.exception(f"chat.diagnose.bootstrap_fail: {e}")
            yield _sse(
                "error",
                ChatErrorPayload(
                    message=f"会话初始化失败: {e.__class__.__name__}"
                ).model_dump(),
            )
            yield _sse("end", {"ok": False})
            await session.rollback()
            return

        system_prompt = build_system_prompt(ipo_code=req.ipo_code)
        emitted_error = False

        try:
            async for evt in agent_graph.run(
                session=session,
                chat_session_id=chat_session.session_id,
                user_message_id=user_msg.message_id,
                user_query=req.question,
                system_prompt=system_prompt,
                history_messages=history_messages,
                model=req.model,
                max_steps=req.max_steps,
                ipo_code=req.ipo_code,
            ):
                if isinstance(evt, agent_graph.StartedEvent):
                    yield _sse(
                        "start",
                        ChatStartPayload(
                            session_id=str(evt.chat_session_id),
                            ipo_code=evt.ipo_code,
                            model=evt.model,
                        ).model_dump(),
                    )
                elif isinstance(evt, agent_graph.TokenDeltaEvent):
                    if evt.text:
                        yield _sse("delta", {"content": evt.text})
                elif isinstance(evt, agent_graph.ToolCallEvent):
                    yield _sse(
                        "tool_call",
                        ChatToolCallPayload(
                            name=evt.name,
                            args=evt.args,
                            status=evt.status,
                            latency_ms=evt.latency_ms,
                            error=evt.error,
                            result_preview=evt.result_preview,
                        ).model_dump(),
                    )
                elif isinstance(evt, agent_graph.FinalAnswerEvent):
                    citations = [
                        ChatCitation(**c.to_dict())
                        for c in evt.citation_bundle.citations
                    ]
                    if citations:
                        yield _sse(
                            "sources",
                            ChatSourcesPayload(citations=citations).model_dump(),
                        )
                    usage = evt.usage_aggregate
                    yield _sse(
                        "end",
                        ChatEndPayload(
                            message_id=str(evt.message_id),
                            finish_reason=evt.finish_reason,
                            usage=ChatTokenUsageDTO(
                                prompt_tokens=usage.prompt_tokens,
                                completion_tokens=usage.completion_tokens,
                                total_tokens=usage.total_tokens,
                                cost_cny=float(usage.cost_cny),
                                llm_call_count=0,  # 不暴露 step 数, 只给聚合
                            ),
                            invalid_citation_indices=evt.invalid_citation_indices,
                        ).model_dump(),
                    )
                elif isinstance(evt, agent_graph.StepErrorEvent):
                    emitted_error = True
                    yield _sse(
                        "error",
                        ChatErrorPayload(message=evt.message).model_dump(),
                    )
                    yield _sse("end", {"ok": False})

        except Exception as e:
            logger.exception(f"chat.diagnose.unhandled: {e}")
            emitted_error = True
            yield _sse(
                "error",
                ChatErrorPayload(
                    message=f"内部错误: {e.__class__.__name__}"
                ).model_dump(),
            )
            yield _sse("end", {"ok": False})

        # 端层事务收尾: 错误时也保留 audit log (主循环已落 chat_messages /
        # chat_tool_calls / chat_token_usage), commit 让审计可追溯
        try:
            if emitted_error:
                # 错误路径: 仍 commit 已落的 user message + 中间 tool calls / token usage
                # 让运营复盘可看到链路在哪一步断的
                await session.commit()
            else:
                await session.commit()
        except Exception as e:
            logger.exception(f"chat.diagnose.commit_fail: {e}")
            await session.rollback()

    return EventSourceResponse(generator(), ping=15)


__all__ = ["router"]
