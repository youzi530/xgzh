"""add chat tables (Sprint 2 BE-S2-001):
chat_sessions / chat_messages / chat_tool_calls / chat_token_usage.

Revision ID: 0002_chat
Revises: 0001_init
Create Date: 2026-04-26

设计说明
========
- 4 张表都用 UUID 主键 + ``gen_random_uuid()`` (来自 0001 已建好的 pgcrypto)
- 时间戳一律 ``TIMESTAMPTZ NOT NULL``, server-side default = now()
- 外键级联策略 (与 ORM 保持一致):
    chat_sessions.user_id          → users.user_id              ON DELETE SET NULL
    chat_messages.session_id       → chat_sessions.session_id   ON DELETE CASCADE
    chat_tool_calls.message_id     → chat_messages.message_id   ON DELETE CASCADE
    chat_token_usage.message_id    → chat_messages.message_id   ON DELETE CASCADE
- Status / role 等枚举字段一律 ``String + comment``, 不用 PG ENUM, 加值不
  需要 ``ALTER TYPE`` (Sprint 1 ipos.status 同方案)
- 索引共 6 个 (spec/09 §BE-S2-001 §3 锁定):
    ix_chat_sessions_user_id_created_at
    ix_chat_sessions_ipo_code_created_at
    ix_chat_messages_session_id_created_at
    ix_chat_tool_calls_tool_name_created_at
    ix_chat_token_usage_model_created_at
    ix_chat_token_usage_created_at
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "0002_chat"
down_revision: str | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- chat_sessions ---
    op.create_table(
        "chat_sessions",
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "users.user_id",
                ondelete="SET NULL",
                name="fk_chat_sessions_user_id_users",
            ),
            nullable=True,
            comment="可空: 支持匿名诊断; 登录后再绑定",
        ),
        sa.Column(
            "ipo_code",
            sa.String(16),
            nullable=True,
            comment="会话锚定的新股 (IPO code, 如 0700.HK / 600519.SH); null = 通用对话",
        ),
        sa.Column(
            "title",
            sa.String(64),
            nullable=False,
            comment="LLM 首问自动生成或用户手动改",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'active'"),
            comment="active/archived/deleted",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_chat_sessions_user_id_created_at",
        "chat_sessions",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_chat_sessions_ipo_code_created_at",
        "chat_sessions",
        ["ipo_code", "created_at"],
    )

    # --- chat_messages ---
    op.create_table(
        "chat_messages",
        sa.Column(
            "message_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "chat_sessions.session_id",
                ondelete="CASCADE",
                name="fk_chat_messages_session_id_chat_sessions",
            ),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.String(16),
            nullable=False,
            comment="user/assistant/tool/system (OpenAI 协议)",
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "openai_tool_call_id",
            sa.String(64),
            nullable=True,
            comment=(
                "OpenAI tool_calls[*].id (string); role='tool' 时引用, "
                "与 chat_tool_calls.tool_call_id (UUID PK) 不是同一字段, "
                "不做外键 (见 spec/09 §BE-S2-001 §5)"
            ),
        ),
        sa.Column(
            "citations",
            JSONB,
            nullable=True,
            comment="[{idx, doc_id, chunk_id, source_url}, ...]; 至多 5-10 项",
        ),
        sa.Column(
            "feedback",
            sa.SmallInteger,
            nullable=True,
            comment="+1/-1/null; Sprint 3 反馈闭环写, Sprint 2 占位",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_chat_messages_session_id_created_at",
        "chat_messages",
        ["session_id", "created_at"],
    )

    # --- chat_tool_calls ---
    op.create_table(
        "chat_tool_calls",
        sa.Column(
            "tool_call_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "message_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "chat_messages.message_id",
                ondelete="CASCADE",
                name="fk_chat_tool_calls_message_id_chat_messages",
            ),
            nullable=False,
            comment="触发本工具调用的 assistant 消息",
        ),
        sa.Column(
            "tool_name",
            sa.String(64),
            nullable=False,
            comment="basic_info/financial/peers/sentiment/historical",
        ),
        sa.Column(
            "args",
            JSONB,
            nullable=True,
            comment="工具入参 (LLM 给的 JSON)",
        ),
        sa.Column(
            "result",
            JSONB,
            nullable=True,
            comment="工具返回 (status='ok' 时填; error/timeout 时为 null)",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'pending'"),
            comment="pending/ok/error/timeout",
        ),
        sa.Column(
            "error_message",
            sa.Text,
            nullable=True,
            comment="status='error'/'timeout' 时填, ≤ 4KB",
        ),
        sa.Column(
            "latency_ms",
            sa.Integer,
            nullable=True,
            comment="执行耗时 (含网络); status='pending' 时为 null",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_chat_tool_calls_tool_name_created_at",
        "chat_tool_calls",
        ["tool_name", "created_at"],
    )

    # --- chat_token_usage ---
    op.create_table(
        "chat_token_usage",
        sa.Column(
            "usage_id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "message_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "chat_messages.message_id",
                ondelete="CASCADE",
                name="fk_chat_token_usage_message_id_chat_messages",
            ),
            nullable=False,
        ),
        sa.Column(
            "model",
            sa.String(64),
            nullable=False,
            comment="LiteLLM 模型名: 'openai/deepseek-ai/DeepSeek-V3' 等",
        ),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column(
            "cost_cny",
            sa.Numeric(10, 6),
            nullable=False,
            comment="本次调用 CNY 成本; 6 位小数 (~¥0.000001 精度足够)",
        ),
        sa.Column(
            "provider",
            sa.String(32),
            nullable=False,
            comment="siliconflow/deepseek/zhipu",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_chat_token_usage_model_created_at",
        "chat_token_usage",
        ["model", "created_at"],
    )
    op.create_index(
        "ix_chat_token_usage_created_at",
        "chat_token_usage",
        ["created_at"],
    )


def downgrade() -> None:
    # 反向顺序: 叶子表 → 根表
    op.drop_table("chat_token_usage")
    op.drop_table("chat_tool_calls")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
