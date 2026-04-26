"""Chat 持久化薄包装 (BE-S2-007 主循环 + 端层共用).

把 ``chat_sessions / chat_messages / chat_tool_calls / chat_token_usage`` 四张表的
"主循环常用 CRUD" 收口到一处, 让 ``graph.py`` / ``api/v1/chat.py`` 不直接 import
ORM 模型, 也不直接拼 SQL.

设计取舍
========
- **每个函数都吃 ``AsyncSession`` 不自起 session**: 主循环里同一个 user query
  对应一个 session 事务 (一次 commit), 函数自起 session 会让"中途 tool 异常"
  导致部分写入残留
- **不在本层 commit**: 端层 ``api/v1/chat.py`` 在 SSE end / error 时统一
  ``await session.commit()`` / ``rollback()``. 主循环只攒 INSERT/UPDATE
- **chat_messages.citations 用 dict 列表**: 与 ``services/agent/citation.py
  ::Citation.to_dict()`` 输出对齐, 端层调一次 ``[c.to_dict() for c in
  citations]`` 即可入库
- **chat_tool_calls 走"先 INSERT pending, 后 UPDATE 终态"**: 与
  ``ChatToolCall`` ORM doc string 锁定. ``insert_tool_call_pending`` 返回
  生成的 ``tool_call_id`` UUID, ``finalize_tool_call`` 接 UUID 写 status / result
- **不在本层做 disclaimer / citation 校验**: 那是 ``citation.py`` + 端层
  responsibility, 持久化只管"把字段塞进去"

不在本 PR 做
============
- chat_sessions.title 自动抽取 (Sprint 3, 用 LLM 一次性总结首问 64 字内)
- chat_messages.feedback 反馈写入 (Sprint 3)
- chat_token_usage 按用户聚合 / billing (BE-S2-008 配额)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm_client import TokenUsage
from app.db.models.chat import (
    ChatMessage,
    ChatSession,
    ChatTokenUsage,
    ChatToolCall,
)

# ─── chat_sessions ────────────────────────────────────────────────────────


async def get_or_create_session(
    session: AsyncSession,
    *,
    session_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    ipo_code: str | None,
    initial_title: str,
) -> ChatSession:
    """续聊给 ``session_id``, 起新会话给 None (返回已 add 的 ChatSession 实例).

    起新会话时 ``initial_title`` 走 user query 前 32 字 (spec/09 锁定的 placeholder
    实现; Sprint 3 再用 LLM 抽). 64 字外硬截.
    """
    if session_id is not None:
        existing = await session.get(ChatSession, session_id)
        if existing is not None:
            return existing
        # session_id 给了但找不到 → 当作起新会话, 强制盖一个新 UUID 防状态错乱

    new_session = ChatSession(
        user_id=user_id,
        ipo_code=ipo_code,
        title=initial_title[:64] if initial_title else "新对话",
        status="active",
    )
    session.add(new_session)
    await session.flush()  # 拿 PK; 不 commit
    return new_session


# ─── chat_messages ────────────────────────────────────────────────────────


async def insert_user_message(
    session: AsyncSession,
    *,
    chat_session_id: uuid.UUID,
    content: str,
) -> ChatMessage:
    """记 user message. 与 assistant 写两次, 让 chat_messages 历史完整."""
    msg = ChatMessage(
        session_id=chat_session_id,
        role="user",
        content=content,
        openai_tool_call_id=None,
        citations=None,
    )
    session.add(msg)
    await session.flush()
    return msg


async def insert_assistant_message(
    session: AsyncSession,
    *,
    chat_session_id: uuid.UUID,
    content: str,
    citations: list[dict[str, Any]] | None = None,
) -> ChatMessage:
    """记 assistant 最终消息 (主循环 reflect 节点收尾时写).

    ``content``: 已经做完 ``ensure_disclaimer`` + ``forbidden_pattern_filter``
    + ``validate_citations_in_text`` 的最终文本. 持久化层不再二次清洗.
    """
    msg = ChatMessage(
        session_id=chat_session_id,
        role="assistant",
        content=content,
        openai_tool_call_id=None,
        citations=citations,
    )
    session.add(msg)
    await session.flush()
    return msg


async def insert_tool_role_message(
    session: AsyncSession,
    *,
    chat_session_id: uuid.UUID,
    openai_tool_call_id: str,
    content: str,
) -> ChatMessage:
    """记 ``role='tool'`` message (主循环 act 节点把 tool 结果回灌 LLM 时写).

    ``content`` 是 ``json.dumps(tool_result.data or {"error": tool_result.error})``,
    这与 OpenAI Chat Completion ``tool`` role 协议对齐, 让历史回放可重放.
    """
    msg = ChatMessage(
        session_id=chat_session_id,
        role="tool",
        content=content,
        openai_tool_call_id=openai_tool_call_id,
        citations=None,
    )
    session.add(msg)
    await session.flush()
    return msg


async def list_session_messages(
    session: AsyncSession,
    *,
    chat_session_id: uuid.UUID,
    limit: int = 50,
) -> list[ChatMessage]:
    """续聊取历史, 按 created_at 升序; 限制最近 N 条防 prompt 爆掉."""
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == chat_session_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return list(rows)


# ─── chat_tool_calls ──────────────────────────────────────────────────────


async def insert_tool_call_pending(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
    tool_name: str,
    args: dict[str, Any] | None,
) -> ChatToolCall:
    """主循环 dispatch tool 前先 INSERT 一行 ``status='pending'``.

    ``message_id`` 必须是触发本调用的 assistant message; 主循环里就是 plan 节
    点刚刚 INSERT 的那条. 返回的实例已 flush, ``tool_call_id`` 已经分配.
    """
    record = ChatToolCall(
        message_id=message_id,
        tool_name=tool_name,
        args=args,
        result=None,
        status="pending",
        error_message=None,
        latency_ms=None,
    )
    session.add(record)
    await session.flush()
    return record


async def finalize_tool_call(
    session: AsyncSession,
    *,
    record: ChatToolCall,
    status: str,
    result: dict[str, Any] | None,
    error_message: str | None,
    latency_ms: int,
) -> None:
    """tool 执行完后写终态 (status / result / error / latency).

    ``status`` ∈ ``{"ok", "error", "timeout"}``. 调用方决定字段:
    - ok: result 非 None, error_message 为 None
    - error / timeout: result 为 None, error_message ≤ 4KB
    """
    record.status = status
    record.result = result
    record.error_message = error_message[:4000] if error_message else None
    record.latency_ms = latency_ms
    # SQLAlchemy 已追踪到该实例, flush 走 UPDATE
    await session.flush()


# ─── chat_token_usage ─────────────────────────────────────────────────────


async def insert_token_usage(
    session: AsyncSession,
    *,
    message_id: uuid.UUID,
    usage: TokenUsage,
    model: str,
    provider: str,
) -> ChatTokenUsage:
    """每次 LLM 调用 (含中间 tool decision step) 都 INSERT 一行.

    cost_cny 走 ``Decimal`` 直接落 ``Numeric(10, 6)``; ``TokenUsage.cost_cny``
    本身就是 ``Decimal`` 类型, 不再转换.
    """
    record = ChatTokenUsage(
        message_id=message_id,
        model=model,
        input_tokens=usage.prompt_tokens,
        output_tokens=usage.completion_tokens,
        cost_cny=usage.cost_cny if isinstance(usage.cost_cny, Decimal) else Decimal(
            str(usage.cost_cny)
        ),
        provider=provider,
    )
    session.add(record)
    await session.flush()
    return record


# ─── 续聊上下文重建 ────────────────────────────────────────────────────────


def message_to_openai_role(msg: ChatMessage) -> dict[str, Any]:
    """ChatMessage → OpenAI Chat Completion ``messages[]`` 单条.

    ``tool`` role 必须带 ``tool_call_id`` (OpenAI 协议要求);
    ``assistant`` role 当时若有 tool_calls, Sprint 2 暂不还原 (只回放最终文本),
    Sprint 3 再补完整 tool_calls 历史 (需要新建关联表把 message ↔ tool_calls
    多对多串起来).
    """
    msg_dict: dict[str, Any] = {"role": msg.role, "content": msg.content or ""}
    if msg.role == "tool" and msg.openai_tool_call_id:
        msg_dict["tool_call_id"] = msg.openai_tool_call_id
    return msg_dict


def session_history_to_messages(
    history: list[ChatMessage],
    *,
    drop_tool_messages: bool = True,
) -> list[dict[str, Any]]:
    """把 ``list_session_messages`` 的输出转 LLM messages 数组.

    ``drop_tool_messages``: 默认丢弃 tool role 历史 (Sprint 2 不能还原对应
    assistant.tool_calls, 单留 tool role 会让 LLM 抱怨"orphan tool message").
    Sprint 3 接上 tool_calls 还原后改 False.
    """
    out: list[dict[str, Any]] = []
    for m in history:
        if drop_tool_messages and m.role == "tool":
            continue
        # system role 历史也跳过: 我们每次主循环新建 system prompt
        if m.role == "system":
            continue
        out.append(message_to_openai_role(m))
    return out


# ─── 工具函数 ──────────────────────────────────────────────────────────────


def _now_utc() -> datetime:
    """统一 utc now (持久化字段大多 server_default=func.now(), 这里是兜底)."""
    return datetime.utcnow()


__all__ = [
    "finalize_tool_call",
    "get_or_create_session",
    "insert_assistant_message",
    "insert_token_usage",
    "insert_tool_call_pending",
    "insert_tool_role_message",
    "insert_user_message",
    "list_session_messages",
    "message_to_openai_role",
    "session_history_to_messages",
]
