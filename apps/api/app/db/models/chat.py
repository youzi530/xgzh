"""AI Agent 会话 / 消息 / 工具调用 / Token 用量 ORM (BE-S2-001).

四张表是 Sprint 2 LangGraph + Tool Use 全链路的底座, 后续 BE-S2-006/007
(Tool 注册中心 + 主循环) 与 BE-S2-008 (配额) / BE-S2-009 (评测) 全部在
这 4 张表上读写. 命名 / 级联 / 索引规则:

总体设计
========
- 主键沿用 Sprint 1 风格 ``<entity>_id`` (UUID, ``gen_random_uuid()``):
    chat_sessions.session_id / chat_messages.message_id /
    chat_tool_calls.tool_call_id / chat_token_usage.usage_id
- ``chat_messages.openai_tool_call_id`` 是 OpenAI ``tool_calls[*].id`` (string),
  与 ``chat_tool_calls.tool_call_id`` (UUID PK) 是两套语义, 显式不做外键
  (spec/09 §BE-S2-001 §5 已锁定): 让 LangGraph 把同一个 string id 同时写到
  message 和 tool_call 两边, SELECT JOIN 时按 ``message_id`` 走主链路.
- 3 个枚举字段 (chat_sessions.status / chat_messages.role /
  chat_tool_calls.status) 一律 ``String + comment + Python Literal``, 不用
  PG ENUM —— Sprint 1 ``ipos.status`` 的成熟方案, 加新值不用 ``ALTER TYPE``,
  改名不用 dump/restore.
- 写入即历史: chat_messages / chat_tool_calls / chat_token_usage 都不带
  TimestampMixin, 只有 ``created_at``. 这避免了 LLM 输出被改写还能蒙混过关
  的伪造空间, 同时减少索引维护成本.
- 软删除: chat_sessions 走业务层 (``status='deleted'``) 而非 deleted_at 列,
  避免 messages 一起被软删后还要在每次查询里加 ``WHERE chat_sessions.deleted_at IS NULL``.

外键级联策略
============
- ``chat_sessions.user_id`` → ``users.user_id`` ``ON DELETE SET NULL``
  (与 invite_codes.owner_user_id 同, 用户注销后会话变匿名, 不丢运营数据)
- ``chat_messages.session_id`` → ``chat_sessions.session_id`` ``CASCADE``
- ``chat_tool_calls.message_id`` → ``chat_messages.message_id`` ``CASCADE``
- ``chat_token_usage.message_id`` → ``chat_messages.message_id`` ``CASCADE``

索引设计 (6 个二级索引, spec/09 锁定)
====================================
- ``chat_sessions(user_id, created_at DESC)``     — 用户最近会话页 (FE-S2-001)
- ``chat_sessions(ipo_code, created_at DESC)``    — 某 IPO 的最近讨论
- ``chat_messages(session_id, created_at)``       — 会话内消息流式拉
- ``chat_tool_calls(tool_name, created_at)``      — 工具用量统计 (运营 / 评测)
- ``chat_token_usage(model, created_at)``         — 按模型成本统计
- ``chat_token_usage(created_at)``                — 时间序列报表 (日 / 周成本)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatSession(Base):
    """AI Agent 用户会话 (一组 user / assistant / tool 消息组成的多轮对话).

    user_id 可空: spec/04 §1.3 允许"匿名也能用 AI", 匿名会话先存 null,
    登录后再绑定 (Sprint 2 不实施绑定流程, 先存 null, Sprint 3+ 接).

    title 自动从首问 LLM 抽 64 字内 (Sprint 2 BE-S2-007 实现);
    用户也可后续手动改名 (Sprint 3+ UI). MVP 阶段约定 ``status='active'``
    一律, ``archived`` / ``deleted`` 是给 Sprint 3+ 会话归档功能预留的状态.

    带 created_at + updated_at: 会话级别允许更新 (改 title / 切 status).
    """

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_user_id_created_at", "user_id", "created_at"),
        Index("ix_chat_sessions_ipo_code_created_at", "ipo_code", "created_at"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        comment="可空: 支持匿名诊断; 登录后再绑定",
    )
    ipo_code: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        comment="会话锚定的新股 (IPO code, 如 0700.HK / 600519.SH); null = 通用对话",
    )
    title: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="LLM 首问自动生成或用户手动改",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
        comment="active/archived/deleted",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ChatMessage(Base):
    """会话内一条消息 (user / assistant / tool / system 任一角色).

    role 取值贴 OpenAI Chat Completion 协议:
    - ``user``      用户输入
    - ``assistant`` LLM 回复 (含 markdown / tool_calls)
    - ``tool``      tool_call 执行结果 (此时 ``openai_tool_call_id`` 必填)
    - ``system``    系统 prompt — 注意我们不存 prompt 全文 (spec/09 §BE-S2-001 §5),
                    这一行只有在调试 / 评测时才会留 (Sprint 3+ Prompt 版本化前临时保留)

    content 是 markdown, 可空字符串但**不可 NULL** (假定调用方提供 ""). 这样
    做让聚合统计 (LENGTH(content) / token 估算) 不需要每次 COALESCE.

    citations: jsonb 数组, 形如::

        [{"idx":1,"doc_id":"prospectus-0700-2025-q1","chunk_id":"...",
          "source_url":"https://hkexnews/..."}]

    feedback: smallint, +1 / -1 / null. Sprint 2 占位字段, BE-S2 不写入,
    Sprint 3 反馈闭环时由 ``POST /chat/messages/{id}/feedback`` 写.

    不带 ``updated_at``: 历史消息不应被改写, 改了就是篡改. content 偶发
    typo 也走"再发一条修正"的对话流, 不在原行原地改.
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index(
            "ix_chat_messages_session_id_created_at",
            "session_id",
            "created_at",
        ),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="user/assistant/tool/system (OpenAI 协议)",
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    openai_tool_call_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment=(
            "OpenAI tool_calls[*].id (string); role='tool' 时引用, "
            "与 chat_tool_calls.tool_call_id (UUID PK) 不是同一字段, "
            "不做外键 (见 spec/09 §BE-S2-001 §5)"
        ),
    )
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="[{idx, doc_id, chunk_id, source_url}, ...]; 至多 5-10 项",
    )
    feedback: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
        comment="+1/-1/null; Sprint 3 反馈闭环写, Sprint 2 占位",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ChatToolCall(Base):
    """LangGraph 工具调用记录 (Tool Use 一次执行的 audit log).

    与 ``chat_messages.openai_tool_call_id`` (string, OpenAI 协议) 同时存在
    但语义不同: 这里 ``tool_call_id`` 是数据库 UUID PK, OpenAI 协议字符串
    那个用来在多轮对话中把 tool_result 关联回去 (LLM 协议层), 数据库 PK
    用来给运营 / 评测做工具用量统计 (analytics 层).

    status 流转: ``pending`` → ``ok`` / ``error`` / ``timeout``;
    BE-S2-007 主循环里执行前 INSERT pending, 执行完 UPDATE 终态; sandbox
    超时(默认 5s) 触发 ``timeout``; 工具内部 raise 触发 ``error``.

    args / result 全是 jsonb (无 schema 限制, 各 tool 自定义); error_message
    存 truncated stack trace (≤ 4KB), 多了对运营无意义.
    """

    __tablename__ = "chat_tool_calls"
    __table_args__ = (
        Index(
            "ix_chat_tool_calls_tool_name_created_at",
            "tool_name",
            "created_at",
        ),
    )

    tool_call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.message_id", ondelete="CASCADE"),
        nullable=False,
        comment="触发本工具调用的 assistant 消息",
    )
    tool_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="basic_info/financial/peers/sentiment/historical",
    )
    args: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="工具入参 (LLM 给的 JSON)",
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="工具返回 (status='ok' 时填; error/timeout 时为 null)",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
        comment="pending/ok/error/timeout",
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="status='error'/'timeout' 时填, ≤ 4KB",
    )
    latency_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="执行耗时 (含网络); status='pending' 时为 null",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class ChatTokenUsage(Base):
    """LLM token 使用 + 成本记账 (成本看板 / 配额结算的基础数据源).

    一次 LLM 调用产生一行: BE-S2-007 主循环每次 ``llm_client.chat()`` 完成
    后 INSERT. 多轮对话 + 多次工具调用一个 user query 产生 N 行,
    ``message_id`` 关联到最终 assistant 消息 (中间步骤可关联 tool 消息).

    cost_cny ``Numeric(10, 6)``: 单次 ~¥0.005-0.05, 6 位小数足够;
    全量聚合 ``sum(cost_cny)::numeric(20,6)`` 不溢出.

    provider 与 model 分两列: 同一 model 名 (如 ``deepseek-chat``) 在不同
    provider (硅基流动 / DeepSeek 官方) 价格不一样, 拆开方便对账.
    """

    __tablename__ = "chat_token_usage"
    __table_args__ = (
        Index(
            "ix_chat_token_usage_model_created_at",
            "model",
            "created_at",
        ),
        Index("ix_chat_token_usage_created_at", "created_at"),
    )

    usage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.message_id", ondelete="CASCADE"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="LiteLLM 模型名: 'openai/deepseek-ai/DeepSeek-V3' 等",
    )
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_cny: Mapped[Decimal] = mapped_column(
        Numeric(10, 6),
        nullable=False,
        comment="本次调用 CNY 成本; 6 位小数 (~¥0.000001 精度足够)",
    )
    provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="siliconflow/deepseek/zhipu",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
