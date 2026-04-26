"""Chat / Agent SSE 端层 (BE-S2-007 + BE-S2-008 配额).

POST ``/api/v1/chat/diagnose`` — ReAct 主循环 SSE 入口.

设计取舍
========
- **匿名友好**: 走 ``get_optional_user``; 没登录也能跑, 但配额更紧 (默认 2/天 IP).
- **会话事务边界**: 端层 ``async with factory() as session`` 起独立 session; 主循环
  只写不 commit, 端层在 SSE end 之前 commit (中间 tool 失败仍保留 audit log).
  错误时 end 后才 rollback (避免 SSE 已 yield 但事务回滚的悖论 — 这里折衷:
  错误事件落表是必要的, 主循环已 logger 记录, DB 持久化失败不阻塞流)
- **DISCLAIMER 兜底位置**: 已在 ``graph.run`` 内 ``ensure_disclaimer`` 处理一次;
  端层不重复 append, 但保留 forbidden_pattern_filter 的"误漏"双保险
- **错误事件 + end 都发**: SSE 客户端约定: ``event=error`` 表示中途异常, 之后
  仍发一个 ``event=end`` 让前端 EventSource ``onmessage`` 走 close 流程
- **配额闸门 (BE-S2-008)**:
  1. 进流前 ``check_quota``; 超额抛 ``HTTPException(429, ChatQuotaExceededResponse)`` —
     不进 SSE 流, 直接 JSON, 让 FE 拿到 429 状态码 + 升级引导 payload
  2. 进流后第一时间 ``record_usage`` 扣额; 失败 (Redis 抖动等) **不阻塞业务**,
     仅 logger.warning, 防"Redis 挂导致全平台 Agent 不可用"
  3. VIP 走 ``record_usage`` noop, 不扣 Redis

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

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
    ChatQuotaExceededResponse,
    ChatQuotaPayload,
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
from app.services.agent.quota import (
    QuotaExceeded,
    QuotaStatus,
    check_quota,
    record_usage,
)
from app.services.agent.system_prompt import build_system_prompt

router = APIRouter(prefix="/chat", tags=["chat"])


def _sse(event_type: str, payload: dict[str, Any]) -> dict[str, str]:
    """SSE 协议: ``{"event": ..., "data": json}``; 中文不转义."""
    return {"event": event_type, "data": json.dumps(payload, ensure_ascii=False)}


def _initial_title_from_query(query: str) -> str:
    """新会话首条 user query 截前 32 字做 title (Sprint 3 用 LLM 抽)."""
    s = query.strip().splitlines()[0] if query else ""
    return s[:32] if s else "新对话"


def _resolve_client_ip(request: Request) -> str | None:
    """匿名 quota key 用的 IP. 优先 ``X-Forwarded-For`` 第一段 (反代场景),
    fallback 到 ``request.client.host`` (直连或本地测试).

    取第一段是因为反向代理链 ``client -> CDN -> nginx -> app`` 时, ``X-F-F``
    会被层层 append, 真实客户端 IP 在最左; 末尾是最近一跳代理.

    对单测的 ASGI Transport, ``request.client`` 总是 ``("testclient", 50000)``,
    返回 ``"testclient"`` 即可 (单测场景无需真 IP, 主要测 user / anon 分支).
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    if request.client is not None:
        return request.client.host
    return None


def _quota_payload(status_obj: QuotaStatus) -> ChatQuotaPayload:
    """``QuotaStatus`` → pydantic ``ChatQuotaPayload`` (用 model_validate 保留校验)."""
    return ChatQuotaPayload.model_validate(status_obj.to_dict())


@router.post(
    "/diagnose",
    responses={
        429: {
            "model": ChatQuotaExceededResponse,
            "description": "Agent 配额超额 (滑动窗口内已用满); FE 弹升级引导 modal",
        },
    },
)
async def chat_diagnose(
    req: ChatDiagnoseRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User | None = Depends(get_optional_user),
) -> EventSourceResponse:
    """流式 Agent 诊断: 用户提交问题 → ReAct 主循环 → SSE 增量 + 工具透传 + 引用源.

    匿名 / 登录都能调, 但配额不同 (匿名 IP 限流默认 2/天, 登录 5/天, VIP 无限).
    超额返回 ``HTTP 429`` + :class:`ChatQuotaExceededResponse` body.
    """
    user_id: uuid.UUID | None = user.user_id if user is not None else None
    anon_key = _resolve_client_ip(request) if user is None else None
    initial_title = _initial_title_from_query(req.question)

    # ── BE-S2-008 配额前置闸门 ────────────────────────────────────
    # 1. check 不写, 拿当前用量; 超额直接 429 (FE 拿到 status code 弹 modal)
    try:
        quota_status = await check_quota(user=user, anon_key=anon_key)
    except Exception as e:  # noqa: BLE001 - Redis 抖动不应阻断, fail-open
        logger.warning(f"chat.diagnose.quota_check_fail (fail-open): {e}")
        quota_status = None

    if quota_status is not None and not quota_status.has_quota:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=ChatQuotaExceededResponse(
                message=(
                    "今日 Agent 调用次数已用完, "
                    + (
                        f"约 {quota_status.retry_after_seconds} 秒后可再次提问"
                        if quota_status.retry_after_seconds is not None
                        else "请稍后再试"
                    )
                    + (
                        ". 升级 VIP 可解除限制."
                        if quota_status.plan.value != "vip"
                        else "."
                    )
                ),
                quota=_quota_payload(quota_status),
            ).model_dump(),
            headers=(
                {"Retry-After": str(quota_status.retry_after_seconds)}
                if quota_status.retry_after_seconds is not None
                else None
            ),
        )

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

        # 进流后第一时间扣额 (在 user_message 写库之后, 让"会话初始化失败"不扣额);
        # Redis 抖动不阻塞业务: fail-open + warn (与 check_quota 一致策略).
        try:
            await record_usage(
                user=user,
                anon_key=anon_key,
                member=str(user_msg.message_id),
            )
        except QuotaExceeded as e:
            # check 已经放行, 但 record 时被并发挤超了 (race 在文档说明里).
            # 此时仍然返 SSE error + end 让前端友好提示; 不重撤回 user_msg
            # (chat_messages 留 audit), 仅滚 Redis 不滚 DB.
            logger.warning(
                f"chat.diagnose.quota_race plan={e.status.plan.value} "
                f"user_id={user_id} retry_after={e.status.retry_after_seconds}"
            )
            yield _sse(
                "error",
                ChatErrorPayload(
                    message=(
                        f"今日 Agent 调用次数已用完, "
                        f"约 {e.status.retry_after_seconds or '稍后'} 秒后可再次提问"
                    )
                ).model_dump(),
            )
            yield _sse("end", {"ok": False, "quota_exceeded": True})
            await session.commit()  # user_msg 仍保留 audit
            return
        except Exception as e:  # noqa: BLE001 - 同 check_quota fail-open
            logger.warning(f"chat.diagnose.record_usage_fail (fail-open): {e}")

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
