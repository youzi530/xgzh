"""BE-S2-001 集成测试: AI Agent 4 张表 schema + ORM CRUD + 级联 + 索引.

覆盖矩阵:
1. ``test_migration_creates_4_tables_with_all_indexes``
   schema_at_head 后, 4 张新表存在 & 6 个二级索引齐
2. ``test_alembic_downgrade_then_upgrade_idempotent``
   ``alembic downgrade -1`` 干净 (4 张表全 drop), 再 upgrade head 全部恢复
3. ``test_chat_session_user_id_set_null_on_user_delete``
   删 user 时 chat_sessions.user_id 变 null (验 SET NULL 而非 CASCADE)
4. ``test_chat_messages_cascade_delete_on_session_delete``
   删 chat_session 后 messages / tool_calls / token_usage 全清光
5. ``test_full_agent_chain_insert_and_select``
   user → assistant + tool_call + tool message + token_usage 一条完整链路
   插入 → 按 session_id 查回, 字段无丢失 (含 jsonb citations / args / result)
6. ``test_chat_messages_no_updated_at_column``
   验消息表是 append-only: 没有 updated_at 列 (写入即历史)

不验:
- pgvector 检索 (BE-S2-003)
- LangGraph 协议 (BE-S2-007)
- API 路由 (Sprint 2 后期)
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from alembic import command
from app.db.models import (
    ChatMessage,
    ChatSession,
    ChatTokenUsage,
    ChatToolCall,
    User,
)

pytestmark = pytest.mark.db


# ─── helper: 构造 alembic Config (与 conftest._build_alembic_config 同) ─────


def _build_alembic_config(test_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", test_url)
    return cfg


_CHAT_TABLES = {
    "chat_sessions",
    "chat_messages",
    "chat_tool_calls",
    "chat_token_usage",
}
_CHAT_INDEXES = {
    "ix_chat_sessions_user_id_created_at",
    "ix_chat_sessions_ipo_code_created_at",
    "ix_chat_messages_session_id_created_at",
    "ix_chat_tool_calls_tool_name_created_at",
    "ix_chat_token_usage_model_created_at",
    "ix_chat_token_usage_created_at",
}


# ─── 1. schema 验证 ───────────────────────────────────────────────────


async def test_migration_creates_4_tables_with_all_indexes(
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """schema_at_head 跑完后 4 张表 + 6 个索引齐."""
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename LIKE 'chat\\_%' ESCAPE '\\'"
            )
        )
        tables = {r[0] for r in rows}
        assert tables == _CHAT_TABLES, (
            f"chat 表缺失或多余: {tables ^ _CHAT_TABLES}"
        )

        rows = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname='public' AND tablename = ANY(:ts)"
            ),
            {"ts": list(_CHAT_TABLES)},
        )
        all_idx = {r[0] for r in rows}
        # 二级索引 + 主键索引 + 外键索引等都在; 我们只断言"二级索引必含"
        missing = _CHAT_INDEXES - all_idx
        assert not missing, f"二级索引缺失: {missing}"


# ─── 2. alembic downgrade / upgrade 幂等 ──────────────────────────────────


async def test_alembic_downgrade_then_upgrade_idempotent(
    test_database_url: str,
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """``alembic downgrade -1`` drop 4 张表 → ``upgrade head`` 恢复.

    重要约束: 测试结束时 schema 必须回到 head, 不然下条同 module 的用例就崩.
    """
    cfg = _build_alembic_config(test_database_url)

    # 0. 起步 (schema_at_head 已跑过): 4 张表都在
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename LIKE 'chat\\_%' ESCAPE '\\'"
            )
        )
        assert {r[0] for r in rows} == _CHAT_TABLES

    # 1. downgrade -1 (回到 0001)
    await asyncio.to_thread(command.downgrade, cfg, "-1")
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename LIKE 'chat\\_%' ESCAPE '\\'"
            )
        )
        assert {r[0] for r in rows} == set(), (
            "downgrade 后 chat_* 表必须 0 个; 残留意味着 downgrade() 写漏"
        )
        # 0001 表应原封不动
        rows = await conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        )
        residual = {r[0] for r in rows}
        assert "users" in residual and "ipos" in residual

    # 2. upgrade 回 head
    try:
        await asyncio.to_thread(command.upgrade, cfg, "head")
        async with db_engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename LIKE 'chat\\_%' ESCAPE '\\'"
                )
            )
            assert {r[0] for r in rows} == _CHAT_TABLES
    except Exception:
        # 兜底: 即便断言失败也要让 schema 回 head, 不污染 module 内后续用例
        await asyncio.to_thread(command.upgrade, cfg, "head")
        raise


# ─── 3. ON DELETE SET NULL: user 删 → chat_sessions.user_id = NULL ────────


async def test_chat_session_user_id_set_null_on_user_delete(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """与 invite_codes.owner_user_id 同策略: 用户被删除时不删会话, 仅匿名化."""
    async with session_factory() as s:
        u = User(phone="+8613800000001", invite_code="TESTCDE1")
        s.add(u)
        await s.flush()
        sess = ChatSession(
            user_id=u.user_id,
            ipo_code="0700.HK",
            title="腾讯控股回港上市分析",
        )
        s.add(sess)
        await s.commit()
        sess_id = sess.session_id
        user_id_to_delete = u.user_id

    # 物理删 user (走 raw SQL 绕过 ORM relationship)
    async with session_factory() as s:
        await s.execute(
            text("DELETE FROM users WHERE user_id = :uid"),
            {"uid": user_id_to_delete},
        )
        await s.commit()

    # 会话仍在, user_id 已变 null
    async with session_factory() as s:
        result = await s.execute(
            select(ChatSession).where(ChatSession.session_id == sess_id)
        )
        kept = result.scalar_one()
        assert kept.user_id is None, (
            "user 删除后 chat_sessions.user_id 必须 SET NULL 而非 CASCADE 删整行"
        )
        assert kept.title == "腾讯控股回港上市分析"
        assert kept.ipo_code == "0700.HK"


# ─── 4. ON DELETE CASCADE: chat_session 删 → messages/tool_calls/usage 全清 ──


async def test_chat_messages_cascade_delete_on_session_delete(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """chat_sessions → chat_messages (CASCADE) → tool_calls + token_usage (CASCADE).

    一次 DELETE chat_sessions 行就该把整条会话下的所有派生数据清光.
    """
    async with session_factory() as s:
        sess = ChatSession(
            user_id=None,
            ipo_code="0700.HK",
            title="临时匿名会话",
        )
        s.add(sess)
        await s.flush()

        msg_user = ChatMessage(
            session_id=sess.session_id,
            role="user",
            content="基本面如何?",
        )
        msg_assistant = ChatMessage(
            session_id=sess.session_id,
            role="assistant",
            content="估值合理...",
            citations=[
                {"idx": 1, "doc_id": "p-0700-q1", "chunk_id": str(uuid.uuid4())}
            ],
        )
        s.add_all([msg_user, msg_assistant])
        await s.flush()

        tc = ChatToolCall(
            message_id=msg_assistant.message_id,
            tool_name="basic_info",
            args={"code": "0700.HK"},
            result={"pe": 22.5},
            status="ok",
            latency_ms=312,
        )
        usage = ChatTokenUsage(
            message_id=msg_assistant.message_id,
            model="openai/deepseek-ai/DeepSeek-V3",
            input_tokens=820,
            output_tokens=156,
            cost_cny=Decimal("0.012345"),
            provider="siliconflow",
        )
        s.add_all([tc, usage])
        await s.commit()
        sess_id = sess.session_id

    # 删 chat_sessions 行
    async with session_factory() as s:
        await s.execute(
            text("DELETE FROM chat_sessions WHERE session_id = :sid"),
            {"sid": sess_id},
        )
        await s.commit()

    # 4 张表全空
    async with session_factory() as s:
        for table in _CHAT_TABLES:
            row = await s.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
            count = row.scalar_one()
            assert count == 0, f"{table} 应全部 CASCADE 清; 实际剩 {count} 行"


# ─── 5. 完整链路 INSERT + SELECT (字段不丢) ────────────────────────────────


async def test_full_agent_chain_insert_and_select(
    session_factory: async_sessionmaker[AsyncSession],
    truncate_all: None,  # noqa: ARG001
) -> None:
    """模拟 LangGraph 一次完整 turn:

    user_msg → assistant_msg(tool_calls=[id=tc-1]) →
      chat_tool_calls(tool_call_id=UUID, openai 那一头串 'tc-1') →
      tool_msg(role='tool', openai_tool_call_id='tc-1') →
      assistant_final_msg → chat_token_usage * 2

    断言: 全部能按 session_id ordered 查回, jsonb 字段精准还原.
    """
    citations_v = [
        {"idx": 1, "doc_id": "p-0700-q1", "chunk_id": "c1", "source_url": "https://x"},
        {"idx": 2, "doc_id": "p-0700-q2", "chunk_id": "c2"},
    ]
    args_v = {"code": "0700.HK", "fields": ["pe", "pb"]}
    # 注: JSONB 不接受 Decimal, 工具结果存 float (后续 Sprint 2 BE-S2-006 也按此约定)
    result_v = {"pe": 22.5, "pb": 3.2}

    async with session_factory() as s:
        u = User(phone="+8613800000099", invite_code="TESTCDE9")
        s.add(u)
        await s.flush()

        sess = ChatSession(
            user_id=u.user_id,
            ipo_code="0700.HK",
            title="腾讯控股深度分析",
        )
        s.add(sess)
        await s.flush()

        msg_user = ChatMessage(
            session_id=sess.session_id,
            role="user",
            content="0700 的基本面?",
        )
        s.add(msg_user)
        await s.flush()

        msg_assistant_pre = ChatMessage(
            session_id=sess.session_id,
            role="assistant",
            content="",  # 决定调工具时, content 可空
        )
        s.add(msg_assistant_pre)
        await s.flush()

        tc = ChatToolCall(
            message_id=msg_assistant_pre.message_id,
            tool_name="basic_info",
            args=args_v,
            result=result_v,
            status="ok",
            latency_ms=180,
        )
        s.add(tc)
        await s.flush()

        msg_tool = ChatMessage(
            session_id=sess.session_id,
            role="tool",
            content='{"pe":22.5,"pb":3.2}',
            openai_tool_call_id="tc-1",
        )
        s.add(msg_tool)
        await s.flush()

        msg_assistant_final = ChatMessage(
            session_id=sess.session_id,
            role="assistant",
            content="估值合理, 风险在游戏监管.",
            citations=citations_v,
        )
        s.add(msg_assistant_final)
        await s.flush()

        s.add_all([
            ChatTokenUsage(
                message_id=msg_assistant_pre.message_id,
                model="openai/deepseek-ai/DeepSeek-V3",
                input_tokens=620,
                output_tokens=42,
                cost_cny=Decimal("0.001234"),
                provider="siliconflow",
            ),
            ChatTokenUsage(
                message_id=msg_assistant_final.message_id,
                model="openai/deepseek-ai/DeepSeek-V3",
                input_tokens=850,
                output_tokens=210,
                cost_cny=Decimal("0.003456"),
                provider="siliconflow",
            ),
        ])
        await s.commit()
        sess_id = sess.session_id

    # SELECT 回来按时序断言
    async with session_factory() as s:
        msgs_q = await s.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == sess_id)
            .order_by(ChatMessage.created_at)
        )
        msgs = list(msgs_q.scalars())
        assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
        assert msgs[2].openai_tool_call_id == "tc-1"
        assert msgs[3].citations == citations_v

        tcs_q = await s.execute(
            select(ChatToolCall).where(ChatToolCall.message_id == msgs[1].message_id)
        )
        tcs = list(tcs_q.scalars())
        assert len(tcs) == 1
        assert tcs[0].tool_name == "basic_info"
        assert tcs[0].args == args_v
        assert tcs[0].status == "ok"
        assert tcs[0].latency_ms == 180

        usage_q = await s.execute(
            select(ChatTokenUsage).order_by(ChatTokenUsage.created_at)
        )
        usages = list(usage_q.scalars())
        assert len(usages) == 2
        assert all(u.provider == "siliconflow" for u in usages)
        # cost_cny 是 Decimal(10,6), 等值比较要用 Decimal
        assert sum((u.cost_cny for u in usages), Decimal("0")) == Decimal("0.004690")


# ─── 6. chat_messages 没有 updated_at 列 ──────────────────────────────────


async def test_chat_messages_no_updated_at_column(
    db_engine: AsyncEngine,
    truncate_all: None,  # noqa: ARG001
) -> None:
    """验消息 / 工具调用 / 用量都是 append-only: 没有 updated_at 列.

    这是审计 / 合规要求 (LLM 输出落库后不可改写). 反过来 chat_sessions
    *允许* updated_at, 因为会话级 status/title 可 mut.
    """
    async with db_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND column_name='updated_at' "
                "AND table_name = ANY(:ts)"
            ),
            {"ts": list(_CHAT_TABLES)},
        )
        tables_with_updated_at = {r[0] for r in rows}
        # 只有 chat_sessions 该有 updated_at; 其余 3 张是 append-only
        assert tables_with_updated_at == {"chat_sessions"}, (
            f"append-only 表不应有 updated_at: 实际 {tables_with_updated_at}"
        )
