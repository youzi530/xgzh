"""``services/agent/persistence.py`` 集成测 (BE-S2-007).

DB 真写真读, 验证 ChatSession / ChatMessage / ChatToolCall / ChatTokenUsage
四张表的 CRUD 薄包装.

不重测 graph.run 端到端 (那由 ``test_chat_diagnose.py`` 覆盖); 这里只盯
"持久化层契约": 字段映射 / 外键 / status 流转 / 历史回放.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.llm_client import TokenUsage
from app.db.models.chat import (
    ChatMessage,
    ChatSession,
    ChatTokenUsage,
    ChatToolCall,
)
from app.services.agent import persistence

# ─── chat_sessions ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_create_session_new(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    async with session_factory() as s:
        cs = await persistence.get_or_create_session(
            s,
            session_id=None,
            user_id=None,
            ipo_code="0700.HK",
            initial_title="腾讯诊断",
        )
        await s.commit()
    assert cs.session_id is not None
    assert cs.title == "腾讯诊断"
    assert cs.status == "active"
    assert cs.ipo_code == "0700.HK"


@pytest.mark.asyncio
async def test_get_or_create_session_resume_existing(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    async with session_factory() as s:
        first = await persistence.get_or_create_session(
            s, session_id=None, user_id=None, ipo_code=None, initial_title="x"
        )
        await s.commit()
        sid = first.session_id

    async with session_factory() as s:
        again = await persistence.get_or_create_session(
            s, session_id=sid, user_id=None, ipo_code="改了", initial_title="新名字"
        )
    # 续聊不改原 session 字段
    assert again.session_id == sid
    assert again.title == "x"


@pytest.mark.asyncio
async def test_get_or_create_session_truncates_long_title(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    long = "标题" * 50
    async with session_factory() as s:
        cs = await persistence.get_or_create_session(
            s, session_id=None, user_id=None, ipo_code=None, initial_title=long
        )
    assert len(cs.title) <= 64


@pytest.mark.asyncio
async def test_get_or_create_session_unknown_id_creates_new(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """给了不存在的 session_id → 起新会话 (防孤儿引用)."""
    bogus = uuid.uuid4()
    async with session_factory() as s:
        cs = await persistence.get_or_create_session(
            s, session_id=bogus, user_id=None, ipo_code=None, initial_title="兜底"
        )
    assert cs.session_id != bogus


# ─── chat_messages ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_user_assistant_messages(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    async with session_factory() as s:
        cs = await persistence.get_or_create_session(
            s, session_id=None, user_id=None, ipo_code=None, initial_title="t"
        )
        u = await persistence.insert_user_message(
            s, chat_session_id=cs.session_id, content="hi"
        )
        a = await persistence.insert_assistant_message(
            s,
            chat_session_id=cs.session_id,
            content="hello",
            citations=[{"idx": 1, "chunk_id": "c-1", "doc_id": "d-1",
                        "ipo_code": "0700.HK", "page": 12, "snippet": "...",
                        "score": 0.9}],
        )
        await s.commit()

    async with session_factory() as s:
        msgs = (
            (await s.execute(select(ChatMessage).order_by(ChatMessage.created_at)))
            .scalars()
            .all()
        )
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].message_id == u.message_id
    assert msgs[1].message_id == a.message_id
    assert msgs[1].citations is not None
    assert len(msgs[1].citations) == 1


@pytest.mark.asyncio
async def test_insert_tool_role_message_keeps_openai_tool_call_id(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    async with session_factory() as s:
        cs = await persistence.get_or_create_session(
            s, session_id=None, user_id=None, ipo_code=None, initial_title="t"
        )
        await persistence.insert_tool_role_message(
            s,
            chat_session_id=cs.session_id,
            openai_tool_call_id="call_abc",
            content='{"data":1}',
        )
        await s.commit()

    async with session_factory() as s:
        m = (
            (
                await s.execute(
                    select(ChatMessage).where(ChatMessage.role == "tool")
                )
            )
            .scalars()
            .one()
        )
    assert m.openai_tool_call_id == "call_abc"
    assert m.role == "tool"


@pytest.mark.asyncio
async def test_list_session_messages_chronological(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    async with session_factory() as s:
        cs = await persistence.get_or_create_session(
            s, session_id=None, user_id=None, ipo_code=None, initial_title="t"
        )
        for i in range(3):
            await persistence.insert_user_message(
                s, chat_session_id=cs.session_id, content=f"q{i}"
            )
        await s.commit()

    async with session_factory() as s:
        msgs = await persistence.list_session_messages(
            s, chat_session_id=cs.session_id
        )
    assert [m.content for m in msgs] == ["q0", "q1", "q2"]


@pytest.mark.asyncio
async def test_session_history_to_messages_drops_tool_role(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    async with session_factory() as s:
        cs = await persistence.get_or_create_session(
            s, session_id=None, user_id=None, ipo_code=None, initial_title="t"
        )
        await persistence.insert_user_message(
            s, chat_session_id=cs.session_id, content="q1"
        )
        await persistence.insert_tool_role_message(
            s, chat_session_id=cs.session_id, openai_tool_call_id="x", content="{}"
        )
        await persistence.insert_assistant_message(
            s, chat_session_id=cs.session_id, content="a1"
        )
        await s.commit()
    async with session_factory() as s:
        history = await persistence.list_session_messages(
            s, chat_session_id=cs.session_id
        )
    msgs = persistence.session_history_to_messages(history)
    roles = [m["role"] for m in msgs]
    assert "tool" not in roles
    assert roles == ["user", "assistant"]


# ─── chat_tool_calls ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_call_pending_to_ok(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    async with session_factory() as s:
        cs = await persistence.get_or_create_session(
            s, session_id=None, user_id=None, ipo_code=None, initial_title="t"
        )
        m = await persistence.insert_assistant_message(
            s, chat_session_id=cs.session_id, content=""
        )
        rec = await persistence.insert_tool_call_pending(
            s,
            message_id=m.message_id,
            tool_name="get_ipo_basic_info",
            args={"code": "0700.HK"},
        )
        assert rec.status == "pending"
        assert rec.tool_call_id is not None
        await persistence.finalize_tool_call(
            s,
            record=rec,
            status="ok",
            result={"code": "0700.HK", "pe": 15.5},
            error_message=None,
            latency_ms=234,
        )
        await s.commit()

    async with session_factory() as s:
        all_calls = (await s.execute(select(ChatToolCall))).scalars().all()
    assert len(all_calls) == 1
    final = all_calls[0]
    assert final.status == "ok"
    assert final.latency_ms == 234
    assert final.result == {"code": "0700.HK", "pe": 15.5}
    assert final.error_message is None


@pytest.mark.asyncio
async def test_tool_call_finalize_error_truncates_message(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    async with session_factory() as s:
        cs = await persistence.get_or_create_session(
            s, session_id=None, user_id=None, ipo_code=None, initial_title="t"
        )
        m = await persistence.insert_assistant_message(
            s, chat_session_id=cs.session_id, content=""
        )
        rec = await persistence.insert_tool_call_pending(
            s, message_id=m.message_id, tool_name="t", args=None
        )
        long = "x" * 5000  # > 4KB
        await persistence.finalize_tool_call(
            s,
            record=rec,
            status="error",
            result=None,
            error_message=long,
            latency_ms=1,
        )
        await s.commit()
    async with session_factory() as s:
        c = (await s.execute(select(ChatToolCall))).scalars().one()
    assert c.error_message is not None
    assert len(c.error_message) <= 4000


# ─── chat_token_usage ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_token_usage_decimal_cost(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    async with session_factory() as s:
        cs = await persistence.get_or_create_session(
            s, session_id=None, user_id=None, ipo_code=None, initial_title="t"
        )
        m = await persistence.insert_assistant_message(
            s, chat_session_id=cs.session_id, content=""
        )
        usage = TokenUsage(
            prompt_tokens=100,
            completion_tokens=200,
            total_tokens=300,
            cost_cny=Decimal("0.012345"),
        )
        await persistence.insert_token_usage(
            s,
            message_id=m.message_id,
            usage=usage,
            model="openai/test-model",
            provider="siliconflow",
        )
        await s.commit()

    async with session_factory() as s:
        rows = (await s.execute(select(ChatTokenUsage))).scalars().all()
    assert len(rows) == 1
    r = rows[0]
    assert r.input_tokens == 100
    assert r.output_tokens == 200
    assert r.cost_cny == Decimal("0.012345")
    assert r.provider == "siliconflow"


# ─── 级联删除 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cascade_delete_session_clears_children(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """删 chat_session 应级联清 messages / tool_calls / token_usage."""
    async with session_factory() as s:
        cs = await persistence.get_or_create_session(
            s, session_id=None, user_id=None, ipo_code=None, initial_title="t"
        )
        m = await persistence.insert_assistant_message(
            s, chat_session_id=cs.session_id, content=""
        )
        await persistence.insert_tool_call_pending(
            s, message_id=m.message_id, tool_name="t", args=None
        )
        await persistence.insert_token_usage(
            s,
            message_id=m.message_id,
            usage=TokenUsage(1, 1, 2, Decimal("0")),
            model="m",
            provider="siliconflow",
        )
        await s.commit()
        sid = cs.session_id

    async with session_factory() as s:
        from sqlalchemy import delete

        await s.execute(delete(ChatSession).where(ChatSession.session_id == sid))
        await s.commit()

    async with session_factory() as s:
        m_left = (await s.execute(select(ChatMessage))).scalars().all()
        tc_left = (await s.execute(select(ChatToolCall))).scalars().all()
        tu_left = (await s.execute(select(ChatTokenUsage))).scalars().all()
    assert m_left == []
    assert tc_left == []
    assert tu_left == []
